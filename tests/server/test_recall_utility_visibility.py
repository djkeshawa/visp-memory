import json

import pytest

from visp_memory.server.app import app
from visp_memory.server.auth import UserContext, get_current_user


@pytest.fixture
def scoped_events(client, monkeypatch):
    storage = app.state.storage
    storage.store_repository({"id": "utility-audit", "name": "Utility", "team_id": "alpha"})
    ids = [
        storage.store_memory(
            f"{team} event", repo_id="utility-audit", metadata={"team_id": team}, auto_link=False
        )
        for team in ("alpha", "beta")
    ]
    for memory_id in ids:
        storage.log_recall_event(memory_id, "surfaced", repo_id="utility-audit")

    async def current_user():
        return UserContext(user_id="alice", username="alice", team_id="alpha")

    monkeypatch.setitem(app.dependency_overrides, get_current_user, current_user)
    return ids


@pytest.mark.asyncio
async def test_repository_utility_inspection_only_aggregates_visible_records(client, scoped_events):
    visible, hidden = scoped_events

    response = await client.get("/recall-events/utility?repo_id=utility-audit")

    assert response.status_code == 200
    report = response.json()
    assert report["summary"]["total_events"] == 1
    assert [event["memory_id"] for event in report["events"]] == [visible]
    assert hidden not in json.dumps(report)


@pytest.mark.asyncio
async def test_repository_utility_verification_only_checks_visible_records(client, scoped_events):
    response = await client.get("/recall-events/verify?repo_id=utility-audit")

    assert response.status_code == 200
    assert response.json()["checked_events"] == 1


@pytest.mark.asyncio
async def test_repository_utility_reset_leaves_hidden_record_events_intact(client, scoped_events):
    visible, hidden = scoped_events

    response = await client.delete("/recall-events?repo_id=utility-audit")

    assert response.status_code == 200
    assert response.json()["deleted"] == 1
    storage = app.state.storage
    assert storage.inspect_recall_utility(memory_id=visible)["summary"]["total_events"] == 0
    assert storage.inspect_recall_utility(memory_id=hidden)["summary"]["total_events"] == 1
