from concurrent.futures import ThreadPoolExecutor

import pytest

from visp_memory.server.app import app
from visp_memory.server.auth_store import AuthStore


@pytest.mark.asyncio
async def test_first_run_requires_private_code_and_consumes_it(client):
    token = app.state.auth_store.get_setup_token()
    data = {"username": "owner", "password": "a-long-test-password", "setup_token": token}
    status = await client.get("/auth/status")
    assert token not in status.text
    wrong = await client.post("/auth/setup", json={**data, "setup_token": "wrong-token-" * 4})
    assert wrong.status_code == 403
    cross_origin = await client.post(
        "/auth/setup", json=data, headers={"Origin": "https://other.test"}
    )
    assert cross_origin.status_code == 403
    created = await client.post("/auth/setup", json=data)
    assert created.status_code == 201
    assert created.json()["role"] == "admin"
    assert data["password"] not in created.text
    assert "password_hash" not in created.json()
    assert (await client.post("/auth/setup", json=data)).status_code == 409
    assert app.state.auth_store.get_setup_token() is None
    assert (await client.post("/auth/login", json=data)).status_code == 200


def test_concurrent_setup_creates_only_one_administrator(tmp_path):
    store = AuthStore(tmp_path / "auth.db")
    token = store.get_setup_token()

    def create(index):
        try:
            store.create_account(
                username=f"owner-{index}",
                password="long-test-password",
                role="admin",
                _setup_token=token,
            )
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sum(executor.map(create, [1, 2])) == 1
    assert len(store.list_accounts()) == 1
