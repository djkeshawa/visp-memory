import pytest

from visp_memory.server.app import app


def scoped_token(scopes):
    account = app.state.auth_store.create_account(
        username="operation-user",
        password="correct-horse-battery-staple",
        role="user",
        team_id="alpha",
    )
    _, token = app.state.auth_store.create_token(
        user_id=account["id"],
        name="operations",
        scopes=scopes,
        repo_ids=["allowed"],
    )
    return account["id"], {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_project_read_token_cannot_start_or_complete_sessions(client):
    owner, headers = scoped_token(["project:read"])
    session_id = app.state.storage.start_session(owner_id=owner, repo_id="allowed")
    read = await client.get(f"/sessions/{session_id}", headers=headers)
    assert read.status_code == 200
    created = await client.post("/sessions", json={"repo_id": "allowed"}, headers=headers)
    completed = await client.post(
        f"/sessions/{session_id}/complete",
        json={"summary": "done"},
        headers=headers,
    )
    assert created.status_code == 403
    assert completed.status_code == 403
    assert app.state.storage.get_session(session_id)["ended_at"] is None


@pytest.mark.asyncio
async def test_project_write_token_can_start_and_complete_sessions(client):
    _, headers = scoped_token(["project:write"])
    created = await client.post("/sessions", json={"repo_id": "allowed"}, headers=headers)
    assert created.status_code == 200
    completed = await client.post(
        f"/sessions/{created.json()['id']}/complete",
        json={"summary": "done"},
        headers=headers,
    )
    assert completed.status_code == 200
    assert app.state.storage.get_session(created.json()["id"])["ended_at"] is not None


@pytest.mark.asyncio
async def test_memory_read_token_can_recall_but_cannot_write(client):
    _, headers = scoped_token(["memory:read"])
    memory_id = app.state.storage.store_memory(
        "database migration guidance",
        repo_id="allowed",
        metadata={"team_id": "alpha"},
        auto_link=False,
    )
    response = await client.post(
        "/recall",
        json={"query": "database migration", "repo_id": "allowed", "min_score": 0},
        headers=headers,
    )
    assert response.status_code == 200
    assert [row["id"] for row in response.json()] == [memory_id]
    write = await client.post(
        "/memories",
        json={"content": "No write access", "repo_id": "allowed"},
        headers=headers,
    )
    assert write.status_code == 403
