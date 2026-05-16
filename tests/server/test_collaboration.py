import pytest

from llm_memory.server.app import app


@pytest.mark.asyncio
async def test_user_and_team_management(client):
    headers = {"X-API-KEY": "test_key"}

    resp_u1 = await client.post(
        "/teams/users",
        json={"username": "alice", "display_name": "Alice"},
        headers=headers,
    )
    assert resp_u1.status_code == 200
    user_id = resp_u1.json()["id"]

    resp_t1 = await client.post("/teams", json={"name": "Alpha Team"}, headers=headers)
    assert resp_t1.status_code == 200
    team_id = resp_t1.json()["id"]

    resp_m = await client.post(
        f"/teams/{team_id}/members",
        json={"user_id": user_id},
        headers=headers,
    )
    assert resp_m.status_code == 200

    resp_ut = await client.get(f"/teams/users/{user_id}/teams", headers=headers)
    assert resp_ut.status_code == 200
    assert len(resp_ut.json()) == 1
    assert resp_ut.json()[0]["name"] == "Alpha Team"


@pytest.mark.asyncio
async def test_team_attribution_and_access(client):
    headers = {"X-API-KEY": "test_key"}

    await client.post(
        "/teams/users",
        json={"username": "alice", "id": "alice-id"},
        headers=headers,
    )
    await client.post("/teams", json={"name": "Alpha", "id": "alpha-id"}, headers=headers)
    await client.post("/teams/alpha-id/members", json={"user_id": "alice-id"}, headers=headers)

    await client.post("/repos", json={"name": "Project X", "id": "repo-x"}, headers=headers)
    await client.post("/repos", json={"name": "Project Alpha", "id": "repo-alpha"}, headers=headers)

    resp = await client.get("/repos?team_id=alpha-id", headers=headers)
    assert len(resp.json()) == 0

    app.state.storage.store_repository(
        {
            "name": "Manual Repo",
            "id": "repo-manual",
            "team_id": "alpha-id",
        }
    )

    resp = await client.get("/repos?team_id=alpha-id", headers=headers)
    assert len(resp.json()) == 1
    assert resp.json()[0]["id"] == "repo-manual"
