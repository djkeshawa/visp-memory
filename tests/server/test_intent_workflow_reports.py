import pytest

from visp_memory.server.app import app
from visp_memory.server.auth import UserContext, get_current_user

HEADERS = {"X-API-KEY": "test_key"}


def report(**changes):
    return {
        "source": "assistant",
        "task_id": "login",
        "event_id": "done-1",
        "revision": 1,
        "status": "completed",
        "summary": "Login work completed",
        "evidence": [{"description": "Login acceptance tests passed"}],
        **changes,
    }


@pytest.mark.asyncio
async def test_report_completes_intent_and_reopen_follows_later_revision(client):
    created = await client.post(
        "/intents", headers=HEADERS, json={"description": "Implement login", "repo_id": "repo-a"}
    )
    intent_id = created.json()["id"]
    url = f"/intents/{intent_id}/workflow-status"
    assert (await client.post(url, json=report())).status_code == 401
    result = await client.post(url, headers=HEADERS, json=report())
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "completed"
    assert result.json()["authoritative"] is False
    assert (await client.get("/intents?repo_id=repo-a", headers=HEADERS)).json() == []
    assert not (await client.post(url, headers=HEADERS, json=report())).json()["applied"]
    stale = await client.post(url, headers=HEADERS, json=report(status="active"))
    assert stale.status_code == 409
    reopened = await client.post(
        url, headers=HEADERS, json=report(status="active", revision=2, event_id="reopen-1")
    )
    assert reopened.status_code == 200
    active = (await client.get("/intents?repo_id=repo-a", headers=HEADERS)).json()
    assert active[0]["id"] == intent_id
    assert len(active[0]["context"]["workflow_history"]) == 2


@pytest.mark.asyncio
async def test_read_only_token_cannot_report_completion(client):
    account = app.state.auth_store.create_account(
        username="owner", password="test-password-long", role="admin"
    )
    _, token = app.state.auth_store.create_token(
        user_id=account["id"], name="read only", scopes=["intent:read"], repo_ids=["repo-a"]
    )
    intent_id = app.state.storage.set_intent("Implement login", repo_id="repo-a")
    result = await client.post(
        f"/intents/{intent_id}/workflow-status",
        json=report(),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert result.status_code == 403


@pytest.mark.asyncio
async def test_non_owner_cannot_become_reporter(client):
    intent_id = app.state.storage.set_intent(
        "Implement login", repo_id="repo-a", context={"author_id": "owner"}
    )

    async def other_user():
        return UserContext(user_id="other", username="other", auth_type="pat", repo_ids=["repo-a"])

    app.dependency_overrides[get_current_user] = other_user
    try:
        result = await client.post(f"/intents/{intent_id}/workflow-status", json=report())
        assert result.status_code in {403, 404}
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
async def test_completion_suggestions_are_persisted_but_do_not_complete(client):
    intent_id = app.state.storage.set_intent("Implement secure login", repo_id="repo-a")
    memory_id = app.state.storage.store_memory(
        "Implemented secure login. Test passed.", repo_id="repo-a", auto_link=False
    )
    response = await client.post(
        f"/intents/{intent_id}/evaluate",
        headers=HEADERS,
        json={"summary": "Implemented secure login. Test passed.", "memory_ids": [memory_id]},
    )
    assert response.status_code == 200
    assert not response.json()["status_changed"]
    suggestions = await client.get(
        "/intents/completion-suggestions?repo_id=repo-a", headers=HEADERS
    )
    assert suggestions.json()[0]["id"] == intent_id
    assert suggestions.json()[0]["status"] == "active"
