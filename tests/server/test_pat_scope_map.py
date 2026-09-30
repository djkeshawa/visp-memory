"""A personal access token holds exactly the scopes its routes ask for.

The mapping used to fall through to ``project:read`` for anything it did not name,
so a token that could only read project metadata could also export the whole
memory graph and search memory content. Every route is now classified on purpose.
"""

import itertools

import pytest

from visp_memory.server.app import app
from visp_memory.server.pat_scopes import DENIED, pat_scope_decision

HEADERS = {"X-API-KEY": "test_key"}
REPO = "repo-a"

# Routes that rely on the compatibility default (project:read) rather than a rule
# of their own. Each returns memory content to a project:read token, and existing
# clients and tests depend on that, so tightening them is a product decision made
# separately from this change; until then they are listed here on purpose so that
# no *other* route can join them unnoticed.
RELIES_ON_DEFAULT = {
    ("GET", "/remember"),
    ("GET", "/reports/memory-intelligence"),
    ("GET", "/reports/memory-intelligence/text"),
}

# The routes whose responses carry memory content or the whole graph, with a
# request that reaches them.
MEMORY_READERS = [
    ("GET", f"/repos/{REPO}/export", None),
    ("POST", "/turn-keys/search", {"query": "x", "repo_id": REPO}),
    ("GET", "/memories/missing/attestation", None),
    ("GET", "/recall-events/utility?repo_id=repo-a", None),
    ("GET", "/recall-events/verify?repo_id=repo-a", None),
]


def route_signatures():
    """Every (method, path template) the app serves through the OpenAPI document."""
    for path, operations in app.openapi()["paths"].items():
        for method in operations:
            if method.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                yield method.upper(), path


def concrete(path: str) -> str:
    return path.replace("{", "").replace("}", "")


_holders = itertools.count()


def pat(scopes, *, admin=False):
    store = app.state.auth_store
    account = store.create_account(
        username=f"holder-{next(_holders)}",
        password="test-password-long",
        role="admin" if admin else "user",
    )
    _, token = store.create_token(
        user_id=account["id"], name="scoped", scopes=list(scopes), repo_ids=[REPO]
    )
    return {"Authorization": f"Bearer {token}"}


def test_every_registered_route_has_an_explicit_scope_decision():
    signatures = list(route_signatures())
    assert len(signatures) > 50  # the walk found the app, not an empty router
    undecided = {
        (method, path)
        for method, path in signatures
        if not pat_scope_decision(method, concrete(path)).explicit
    } - RELIES_ON_DEFAULT
    assert not undecided, (
        "These routes fall through to the project:read default; classify them in "
        f"server/pat_scopes.py: {sorted(undecided)}"
    )


def test_the_allowlist_only_names_routes_that_really_use_the_default():
    # A stale entry would hide a route that has since been classified.
    assert {
        sig for sig in RELIES_ON_DEFAULT
        if not pat_scope_decision(sig[0], sig[1]).explicit
    } == RELIES_ON_DEFAULT


def test_an_unclassified_path_still_defaults_to_project_read():
    decision = pat_scope_decision("GET", "/not-a-route")
    assert decision.scopes == ("project:read",)
    assert decision.explicit is False


@pytest.mark.parametrize(
    "method,path,scopes",
    [
        ("GET", "/repos/r/export", {"project:read", "memory:read", "intent:read"}),
        ("POST", "/turn-keys/search", {"memory:read"}),
        ("GET", "/memories/m/attestation", {"memory:read"}),
        ("GET", "/recall-events/utility", {"memory:read"}),
        ("GET", "/recall-events/verify", {"memory:read"}),
        ("POST", "/recall-events", {"memory:write"}),
        ("DELETE", "/recall-events", {"memory:write"}),
        ("GET", "/intents/usage", {"intent:read"}),
        ("GET", "/diagnostics/capabilities", {"project:read"}),
    ],
)
def test_the_new_routes_ask_for_the_scope_their_data_needs(method, path, scopes):
    assert set(pat_scope_decision(method, path).scopes) == scopes


def test_a_graph_import_is_never_reachable_by_a_token():
    for scope in ("*", "admin", "project:write", "memory:write"):
        assert pat_scope_decision("POST", "/repos/r/import").kind == DENIED, scope


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path,payload", MEMORY_READERS)
async def test_a_project_read_token_cannot_read_memory(client, method, path, payload):
    response = await client.request(
        method, path, json=payload, headers=pat(["project:read"])
    )
    assert response.status_code == 403, response.text
    assert "memory:read" in response.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path,payload", MEMORY_READERS)
async def test_a_token_with_the_right_scopes_is_let_through(client, method, path, payload):
    app.state.storage.store_memory("Readable", repo_id=REPO, auto_link=False)
    response = await client.request(
        method,
        path,
        json=payload,
        headers=pat(["project:read", "memory:read", "intent:read"]),
    )
    assert response.status_code != 403, response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["POST", "DELETE"])
async def test_recall_event_writes_need_memory_write(client, method):
    response = await client.request(
        method,
        "/recall-events",
        json={"repo_id": REPO, "query": "q", "returned_memory_ids": []},
        headers=pat(["memory:read", "project:write"]),
    )
    assert response.status_code == 403
    assert "memory:write" in response.json()["detail"]


@pytest.mark.asyncio
async def test_intent_usage_needs_intent_read(client):
    denied = await client.get("/intents/usage", headers=pat(["memory:read"]))
    allowed = await client.get("/intents/usage", headers=pat(["intent:read"]))
    assert denied.status_code == 403
    assert allowed.status_code != 403


@pytest.mark.asyncio
async def test_capabilities_are_readable_by_a_non_admin_token(client):
    response = await client.get("/diagnostics/capabilities", headers=pat(["project:read"]))
    assert response.status_code == 200, response.text
    storage = await client.get("/diagnostics/storage", headers=pat(["project:read"]))
    assert storage.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("scopes", [["*"], ["admin", "project:write"]])
async def test_no_token_reaches_the_graph_import_even_an_admin_one(client, scopes):
    response = await client.post(
        f"/repos/{REPO}/import", json={"version": "3.0"}, headers=pat(scopes, admin=True)
    )
    assert response.status_code == 403
    assert "cannot import" in response.json()["detail"]


@pytest.mark.asyncio
async def test_the_api_key_admin_can_still_import(client):
    response = await client.post(
        f"/repos/{REPO}/import", json={"version": "3.0"}, headers=HEADERS
    )
    assert response.status_code != 403, response.text
