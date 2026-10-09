"""Registering a scope must not hand another team's records to the registrant.

A write that names an unregistered project leaves a placeholder row, and explicit
registration takes that row over and stamps the registrant's team on it. Before
this was guarded, any authenticated user could ``POST /repos {"id": "proj"}`` for
another team's project: the owning team then got 404s, and every untagged record
in the scope became visible to the claimant.
"""

import pytest

from visp_memory.core.storage import is_implicitly_registered
from visp_memory.server.app import app

SCOPES = ["project:read", "project:write", "memory:read", "memory:write"]


def _token(username: str, team_id: str) -> dict[str, str]:
    account = app.state.auth_store.create_account(
        username=username, password="correct-horse-battery-staple", team_id=team_id
    )
    _, token = app.state.auth_store.create_token(
        user_id=account["id"], name=username, scopes=SCOPES, repo_ids=[]
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def teams(client):
    return _token("alice", "team-a"), _token("mallory", "team-b")


async def _remember(client, headers, content="team A deploy secret"):
    response = await client.post(
        "/memories",
        json={"content": content, "category": "decision", "repo_id": "proj"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


@pytest.mark.asyncio
async def test_another_team_cannot_claim_a_scope_holding_our_records(client, teams):
    alice, mallory = teams
    memory_id = await _remember(client, alice)

    claim = await client.post("/repos", json={"id": "proj", "name": "proj"}, headers=mallory)

    assert claim.status_code == 404
    assert is_implicitly_registered(app.state.storage.get_repository("proj"))
    still_ours = await client.get(f"/memories/{memory_id}", headers=alice)
    assert still_ours.status_code == 200


@pytest.mark.asyncio
async def test_untagged_records_block_a_non_admin_claim(client, teams):
    _, mallory = teams
    app.state.storage.store_memory("written by the CLI", repo_id="proj", auto_link=False)

    claim = await client.post("/repos", json={"id": "proj", "name": "proj"}, headers=mallory)

    assert claim.status_code == 404


@pytest.mark.asyncio
async def test_a_team_can_register_the_scope_its_own_records_created(client, teams):
    alice, mallory = teams
    await _remember(client, alice)

    claim = await client.post("/repos", json={"id": "proj", "name": "Project"}, headers=alice)

    assert claim.status_code == 200, claim.text
    assert app.state.storage.get_repository("proj")["team_id"] == "team-a"
    hidden = await client.get("/repos/proj", headers=mallory)
    assert hidden.status_code == 404


@pytest.mark.asyncio
async def test_registration_cannot_mark_itself_implicit(client, teams):
    alice, mallory = teams
    await _remember(client, alice)

    claim = await client.post(
        "/repos",
        json={"id": "proj", "name": "proj", "metadata": {"registration": "implicit", "k": 1}},
        headers=alice,
    )

    assert claim.status_code == 200, claim.text
    stored = app.state.storage.get_repository("proj")
    assert not is_implicitly_registered(stored)
    assert stored["metadata"] == {"k": 1}
    takeover = await client.post("/repos", json={"id": "proj", "name": "proj"}, headers=mallory)
    assert takeover.status_code == 404
