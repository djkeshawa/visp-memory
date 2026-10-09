"""Password sign-in: off the event loop, rate limited, no username timing oracle."""

import threading
from unittest import mock

import pytest

from visp_memory.server.app import app
from visp_memory.server.auth_store import AuthStore
from visp_memory.server.login_throttle import (
    MAX_FAILURES_PER_ACCOUNT,
    MAX_FAILURES_PER_ADDRESS,
    WINDOW_SECONDS,
    LoginThrottle,
)

PASSWORD = "correct-horse-battery-staple"


# --- timing oracle ---------------------------------------------------------------


@pytest.mark.parametrize("username", ["nobody", "disabled"])
def test_an_unknown_or_disabled_user_still_costs_a_hash_verification(tmp_path, username):
    store = AuthStore(tmp_path / "auth.db")
    account = store.create_account(username="disabled", password=PASSWORD)
    store.update_account(account["id"], enabled=False)

    store.passwords = mock.Mock(wraps=store.passwords)

    assert store.authenticate_password(username, PASSWORD) is None

    store.passwords.verify.assert_called_once()


# --- event loop ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_password_verification_runs_off_the_event_loop(client):
    loop_thread = threading.get_ident()
    seen = []

    def authenticate(username, password):
        seen.append(threading.get_ident())

    with mock.patch.object(app.state.auth_store, "authenticate_password", authenticate):
        await client.post("/auth/login", json={"username": "who", "password": "x"})

    assert seen and seen[0] != loop_thread


# --- rate limit ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_repeated_failures_are_refused_even_with_the_right_password(client):
    app.state.auth_store.create_account(username="owner", password=PASSWORD)
    for _ in range(MAX_FAILURES_PER_ACCOUNT):
        wrong = await client.post("/auth/login", json={"username": "owner", "password": "no"})
        assert wrong.status_code == 401

    throttled = await client.post("/auth/login", json={"username": "owner", "password": PASSWORD})

    assert throttled.status_code == 429
    assert int(throttled.headers["retry-after"]) > 0


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_the_window_expires():
    clock = _Clock()
    throttle = LoginThrottle(clock=clock)
    for _ in range(MAX_FAILURES_PER_ACCOUNT):
        throttle.record_failure("10.0.0.1", "owner")
    assert throttle.retry_after("10.0.0.1", "owner") > 0

    clock.now += WINDOW_SECONDS + 1

    assert throttle.retry_after("10.0.0.1", "owner") == 0


def test_another_address_is_not_locked_out_of_the_account():
    throttle = LoginThrottle(clock=_Clock())
    for _ in range(MAX_FAILURES_PER_ACCOUNT):
        throttle.record_failure("10.0.0.1", "Owner")

    assert throttle.retry_after("10.0.0.1", "owner") > 0
    assert throttle.retry_after("10.0.0.2", "owner") == 0


def test_spraying_many_usernames_from_one_address_is_bounded():
    throttle = LoginThrottle(clock=_Clock())
    for index in range(MAX_FAILURES_PER_ADDRESS):
        throttle.record_failure("10.0.0.1", f"user-{index}")

    assert throttle.retry_after("10.0.0.1", "fresh-user") > 0


def test_success_clears_the_account_count():
    throttle = LoginThrottle(clock=_Clock())
    for _ in range(MAX_FAILURES_PER_ACCOUNT - 1):
        throttle.record_failure("10.0.0.1", "owner")
    throttle.record_success("10.0.0.1", "owner")
    throttle.record_failure("10.0.0.1", "owner")

    assert throttle.retry_after("10.0.0.1", "owner") == 0
