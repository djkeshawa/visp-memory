import pytest

from visp_memory.server.app import app
from visp_memory.server.auth import UserContext, get_current_user

HEADERS = {"X-API-KEY": "test_key"}


def duplicate_notes():
    return [
        app.state.storage.store_memory("Repeatable backup note", repo_id="repo-a", auto_link=False)
        for _ in range(2)
    ]


@pytest.mark.asyncio
async def test_dreaming_api_preview_schedule_run_and_undo(client):
    ids = duplicate_notes()
    preview = await client.get("/dreaming/repo-a/preview", headers=HEADERS)
    assert preview.status_code == 200, preview.text
    assert all(app.state.storage.get_memory(mid)["status"] == "active" for mid in ids)
    schedule = await client.put(
        "/dreaming/repo-a/schedule", headers=HEADERS, json={"enabled": True, "interval_hours": 12}
    )
    assert schedule.json()["enabled"]
    result = await client.post("/dreaming/repo-a/run", headers=HEADERS)
    assert result.status_code == 200, result.text
    action = result.json()["proposals"][0]["action_id"]
    assert sum(app.state.storage.get_memory(mid)["status"] == "merged" for mid in ids) == 1
    undo = await client.post(f"/dreaming/repo-a/actions/{action}/undo", headers=HEADERS)
    assert undo.status_code == 200, undo.text
    assert all(app.state.storage.get_memory(mid)["status"] == "active" for mid in ids)
    history = (await client.get("/dreaming/repo-a", headers=HEADERS)).json()
    assert history["runs"][0]["proposals"][0]["resolution"] == "undone"


@pytest.mark.asyncio
async def test_dreaming_denies_non_admin_and_read_only_token(client):
    duplicate_notes()
    account = app.state.auth_store.create_account(
        username="read-only", password="long-test-password", role="admin"
    )
    _, token = app.state.auth_store.create_token(
        user_id=account["id"], name="read", scopes=["memory:read"], repo_ids=["repo-a"]
    )
    response = await client.post(
        "/dreaming/repo-a/run", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 403

    async def ordinary_user():
        return UserContext(user_id="reader", username="reader", auth_type="session", is_admin=False)

    app.dependency_overrides[get_current_user] = ordinary_user
    try:
        response = await client.get("/dreaming/repo-a")
        assert response.status_code == 403
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
async def test_unknown_archived_and_cross_project_actions_are_refused(client):
    duplicate_notes()
    response = await client.post("/dreaming/missing/run", headers=HEADERS)
    assert response.status_code == 409
    result = (await client.post("/dreaming/repo-a/run", headers=HEADERS)).json()
    app.state.storage.store_memory("Other project", repo_id="repo-b", auto_link=False)
    action = result["proposals"][0]["action_id"]
    assert (
        await client.post(f"/dreaming/repo-b/actions/{action}/undo", headers=HEADERS)
    ).status_code == 409
    with app.state.storage._get_db() as conn:
        conn.execute("UPDATE repositories SET status = 'archived' WHERE id = 'repo-a'")
        conn.commit()
    assert (await client.post("/dreaming/repo-a/run", headers=HEADERS)).status_code == 409


@pytest.mark.asyncio
async def test_schedule_rejects_unbounded_frequency(client):
    duplicate_notes()
    response = await client.put(
        "/dreaming/repo-a/schedule", headers=HEADERS, json={"enabled": True, "interval_hours": 0}
    )
    assert response.status_code == 422
