"""Gaps a token or session could use to reach what its scopes or role do not allow.

Starlette routes ``/memories%0A`` to ``/memories`` (its route regex ends in ``$``,
which matches before a trailing newline), but the scope table's ``fullmatch`` did
not, so such a path fell through to the ``project:read`` default for every method.
"""

import itertools
from unittest import mock

import pytest

from visp_memory.server import app as server_app
from visp_memory.server.app import app
from visp_memory.server.auth import UserContext, get_current_user
from visp_memory.server.control_path_guard import ControlPathGuardMiddleware
from visp_memory.server.pat_scopes import DENIED, PatDecision, pat_scope_decision

REPO = "repo-a"
OTHER = "repo-b"
API_KEY = {"X-API-KEY": "test_key"}

_holders = itertools.count()


def pat(scopes, *, admin=False, team="alpha", repo_ids=(REPO,)):
    store = app.state.auth_store
    account = store.create_account(
        username=f"gap-holder-{next(_holders)}",
        password="test-password-long",
        role="admin" if admin else "user",
        team_id=team,
    )
    _, token = store.create_token(
        user_id=account["id"], name="scoped", scopes=list(scopes), repo_ids=list(repo_ids)
    )
    return {"Authorization": f"Bearer {token}"}


async def _passthrough(self, scope, receive, send):
    await self.app(scope, receive, send)


# Lets a test reach the layers behind the path guard, to show each holds alone.
_GUARD_BYPASSED = mock.patch.object(ControlPathGuardMiddleware, "__call__", _passthrough)
_TABLE_BYPASSED = mock.patch(
    "visp_memory.server.auth.pat_scope_decision", return_value=PatDecision("open")
)


# --- 1. Control characters in the path ---------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,path,payload",
    [
        ("GET", "/memories%0A?repo_id=repo-a", None),
        ("POST", "/memories%0A", {"content": "written by a read token", "repo_id": REPO}),
        ("POST", "/intents%0A", {"description": "planted", "repo_id": REPO}),
        ("POST", "/teams%0A", {"name": "evil"}),
        ("GET", "/memories%09", None),
        ("GET", "/memories%7F", None),
    ],
)
async def test_a_path_with_a_control_character_is_refused(client, method, path, payload):
    response = await client.request(
        method, path, json=payload, headers=pat(["project:read"], admin=True)
    )
    assert response.status_code == 400, response.text
    assert app.state.storage.list_memories(repo_id=REPO) == []
    assert app.state.storage.get_active_intents(repo_id=REPO, status="all") == []


@pytest.mark.asyncio
async def test_an_oversized_import_cannot_slip_past_the_limit_with_a_newline(
    client, monkeypatch
):
    monkeypatch.setattr(server_app.config.server, "max_import_body_bytes", 16)
    body = b'{"version":"3.0","pad":"larger than the limit"}'
    response = await client.post(
        f"/repos/{REPO}/import%0A",
        content=body,
        headers={**API_KEY, "Content-Type": "application/json"},
    )
    assert response.status_code == 400, response.text


def test_an_unclassified_write_is_refused_by_the_scope_table():
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        decision = pat_scope_decision(method, "/memories\n")
        assert decision.kind == DENIED and decision.explicit is False, method
    # Reads keep the documented project:read default.
    assert pat_scope_decision("GET", "/memories\n").scopes == ("project:read",)


@pytest.mark.asyncio
async def test_behind_the_guard_a_read_token_still_cannot_write(client):
    with _GUARD_BYPASSED:
        response = await client.post(
            "/memories%0A",
            json={"content": "written by a read token", "repo_id": REPO},
            headers=pat(["project:read"]),
        )
    assert response.status_code == 403, response.text
    assert app.state.storage.list_memories(repo_id=REPO) == []


@pytest.mark.asyncio
async def test_team_creation_needs_an_admin_scope_not_only_an_admin_role(client):
    with _TABLE_BYPASSED:
        response = await client.post(
            "/teams", json={"name": "evil"}, headers=pat(["project:read"], admin=True)
        )
    assert response.status_code == 403, response.text
    assert "admin" in response.json()["detail"]


# --- 2. Read-only POSTs ask for read scopes ----------------------------------


@pytest.mark.asyncio
async def test_graph_recall_needs_memory_read_not_memory_write(client):
    memory_id = app.state.storage.store_memory(
        "deploy key rotation procedure", repo_id=REPO, auto_link=False,
        metadata={"team_id": "alpha"},
    )
    payload = {"memory_id": memory_id, "repo_id": REPO}
    read = await client.post(
        "/graph-recall/neighbors", json=payload, headers=pat(["memory:read"])
    )
    write = await client.post(
        "/graph-recall/neighbors", json=payload, headers=pat(["memory:write"])
    )
    assert read.status_code == 200, read.text
    assert write.status_code == 403, write.text
    assert "memory:read" in write.json()["detail"]


@pytest.mark.asyncio
async def test_ask_needs_memory_read_as_well_as_write(client):
    app.state.storage.store_memory(
        "deploy key rotation procedure", repo_id=REPO, auto_link=False,
        metadata={"team_id": "alpha"},
    )
    payload = {"query": "deploy key rotation", "repo_id": REPO}
    write_only = await client.post("/ai/ask", json=payload, headers=pat(["memory:write"]))
    read_only = await client.post("/ai/ask", json=payload, headers=pat(["memory:read"]))
    both = await client.post(
        "/ai/ask", json=payload, headers=pat(["memory:read", "memory:write"])
    )
    assert write_only.status_code == 403
    assert "memory:read" in write_only.json()["detail"]
    assert read_only.status_code == 403
    assert "memory:write" in read_only.json()["detail"]
    assert both.status_code == 200, both.text


# --- 3. Intent context cannot name another team -------------------------------


@pytest.mark.asyncio
async def test_a_teamless_user_cannot_plant_an_intent_in_another_team(client):
    beta = pat(["intent:read", "intent:write"], team="beta", repo_ids=())
    await client.post("/intents", json={"description": "beta intent", "repo_id": "proj"},
                      headers=beta)
    teamless = pat(["intent:write"], team=None, repo_ids=())
    response = await client.post(
        "/intents",
        json={
            "description": "planted",
            "repo_id": "proj",
            "context": {"team_id": "beta", "author_id": "someone", "note": "kept"},
        },
        headers=teamless,
    )
    assert response.status_code == 200, response.text
    context = response.json()["context"]
    assert "team_id" not in context
    assert context["author_id"] != "someone"
    assert context["note"] == "kept"

    listed = await client.get("/intents?repo_id=proj", headers=beta)
    assert [i["description"] for i in listed.json()] == ["beta intent"]


# --- 4. Project-restricted admin tokens stay in their projects ----------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,path,payload",
    [
        ("GET", f"/platform/audit-log?repo_id={OTHER}", None),
        ("GET", "/platform/audit-log", None),
        ("GET", f"/diagnostics/embedding-index?repo_id={OTHER}", None),
        ("GET", "/diagnostics/embedding-index", None),
        ("POST", "/diagnostics/embedding-index/reindex", {"repo_id": OTHER, "dry_run": True}),
        ("POST", "/diagnostics/embedding-index/reindex", {"dry_run": True}),
    ],
)
async def test_a_restricted_admin_token_cannot_reach_other_projects(
    client, method, path, payload
):
    headers = pat(["admin", "project:write", "memory:write"], admin=True)
    response = await client.request(method, path, json=payload, headers=headers)
    assert response.status_code == 404, response.text


@pytest.mark.asyncio
async def test_a_restricted_admin_token_still_reads_its_own_project(client):
    headers = pat(["admin", "project:write", "memory:write"], admin=True)
    response = await client.get(f"/platform/audit-log?repo_id={REPO}", headers=headers)
    assert response.status_code == 200, response.text


# --- 5. Storage and provider diagnostics are for administrators ---------------


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/diagnostics/storage", "/diagnostics/providers"])
async def test_backend_diagnostics_refuse_a_non_admin_session(client, path):
    async def non_admin():
        return UserContext(user_id="alice", username="alice", team_id="alpha", is_admin=False)

    app.dependency_overrides[get_current_user] = non_admin
    try:
        refused = await client.get(path)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    allowed = await client.get(path, headers=API_KEY)

    assert refused.status_code == 403, refused.text
    assert allowed.status_code == 200, allowed.text
