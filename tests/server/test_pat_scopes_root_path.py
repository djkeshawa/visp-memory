"""A token's scopes are enforced on the path Starlette routes, not the raw URL.

Behind ``uvicorn --root-path`` or a prefix-stripping proxy the request URL still
carries the prefix (``/x/auth/tokens``) while the router matches the stripped
path (``/auth/tokens``). Deciding scopes on the URL made every prefixed path miss
the table and fall through to the ``project:read`` default, so a read-only token
could mint a write token or store memories.
"""

import itertools
from unittest import mock

import httpx
import pytest

from visp_memory.server.app import app
from visp_memory.server.pat_scopes import PatDecision

REPO = "repo-a"
ROOT = "/x"

_holders = itertools.count()


def pat(scopes, *, admin=False):
    store = app.state.auth_store
    account = store.create_account(
        username=f"prefixed-{next(_holders)}",
        password="test-password-long",
        role="admin" if admin else "user",
    )
    _, token = store.create_token(
        user_id=account["id"], name="scoped", scopes=list(scopes), repo_ids=[REPO]
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def prefixed(client):
    """The shared test app, reached through a ``root_path`` of ``/x``."""
    transport = httpx.ASGITransport(app=app, root_path=ROOT)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


@pytest.mark.asyncio
async def test_a_read_token_cannot_mint_a_token_under_a_root_path(prefixed):
    response = await prefixed.post(
        f"{ROOT}/auth/tokens",
        json={"name": "escalated", "scopes": ["memory:write", "project:write"]},
        headers=pat(["project:read"]),
    )
    assert response.status_code == 403, response.text


@pytest.mark.asyncio
async def test_a_read_token_cannot_store_memory_under_a_root_path(prefixed):
    response = await prefixed.post(
        f"{ROOT}/memories",
        json={"content": "written through a prefix", "repo_id": REPO},
        headers=pat(["project:read"]),
    )
    assert response.status_code == 403, response.text
    assert "memory:write" in response.json()["detail"]


@pytest.mark.asyncio
async def test_the_export_still_needs_memory_read_under_a_root_path(prefixed):
    response = await prefixed.get(
        f"{ROOT}/repos/{REPO}/export", headers=pat(["project:read"])
    )
    assert response.status_code == 403, response.text
    assert "memory:read" in response.json()["detail"]


@pytest.mark.asyncio
async def test_no_token_imports_a_graph_under_a_root_path(prefixed):
    response = await prefixed.post(
        f"{ROOT}/repos/{REPO}/import",
        json={"version": "3.0"},
        headers=pat(["*"], admin=True),
    )
    assert response.status_code == 403, response.text
    assert "cannot import" in response.json()["detail"]


@pytest.mark.asyncio
async def test_the_right_scopes_still_pass_under_a_root_path(prefixed):
    # The control: the prefix is routed, so a correctly scoped token gets through.
    response = await prefixed.get(
        f"{ROOT}/repos/{REPO}/export",
        headers=pat(["project:read", "memory:read", "intent:read"]),
    )
    assert response.status_code not in {403, 404}, response.text


@pytest.mark.asyncio
async def test_dreaming_requests_are_not_activity_under_a_root_path(prefixed):
    # The idle check that lets dreaming run keys on the routed path too.
    app.state.dream_last_activity = 0.0
    await prefixed.post(f"{ROOT}/dreaming/{REPO}/run")
    assert app.state.dream_last_activity == 0.0
    await prefixed.post(f"{ROOT}/memories", json={"content": "x"})
    assert app.state.dream_last_activity > 0.0


# --- Defence in depth: the account and token handlers refuse a token themselves.

_TABLE_BYPASSED = mock.patch(
    "visp_memory.server.auth.pat_scope_decision",
    return_value=PatDecision("open"),
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,path,payload",
    [
        ("GET", "/auth/tokens", None),
        ("POST", "/auth/tokens", {"name": "escalated", "scopes": ["memory:write"]}),
        ("DELETE", "/auth/tokens/any", None),
        ("GET", "/auth/users", None),
        ("POST", "/auth/users", {"username": "someone", "password": "long-enough-pass"}),
        ("PATCH", "/auth/users/any", {"enabled": False}),
        ("POST", "/auth/users/any/password", {"password": "long-enough-pass"}),
        ("POST", "/auth/logout", None),
    ],
)
async def test_account_handlers_refuse_a_token_even_if_the_table_is_bypassed(
    client, method, path, payload
):
    headers = pat(["*", "admin"], admin=True)
    with _TABLE_BYPASSED:
        response = await client.request(method, path, json=payload, headers=headers)
    assert response.status_code == 403, response.text
    assert "session" in response.json()["detail"]


@pytest.mark.asyncio
async def test_a_token_can_still_read_its_own_identity(client):
    response = await client.get("/auth/me", headers=pat(["project:read"]))
    assert response.status_code == 200, response.text
    assert response.json()["auth_type"] == "pat"
