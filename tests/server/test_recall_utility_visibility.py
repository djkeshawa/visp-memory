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


@pytest.mark.asyncio
async def test_empty_event_filter_is_invalid_even_without_visible_records(client, scoped_events):
    storage = app.state.storage
    storage.update_memory(scoped_events[0], metadata={"team_id": "beta"})

    response = await client.get("/recall-events/utility?repo_id=utility-audit&event_type=")

    assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("event_type", ["task-linked", "outcome-linked"])
@pytest.mark.parametrize("operation", ["inspect", "verify", "reset"])
async def test_utility_filters_preserve_supported_hyphenated_event_names(
    client, scoped_events, event_type, operation
):
    visible, hidden = scoped_events
    storage = app.state.storage
    for memory_id in scoped_events:
        storage.log_recall_event(memory_id, event_type, repo_id="utility-audit")
    params = {"repo_id": "utility-audit", "event_type": event_type}

    if operation == "inspect":
        response = await client.get("/recall-events/utility", params=params)
    elif operation == "verify":
        response = await client.get("/recall-events/verify", params=params)
    else:
        response = await client.delete("/recall-events", params=params)

    assert response.status_code == 200
    report = response.json()
    if operation == "inspect":
        assert report["summary"]["total_events"] == 1
        assert report["summary"]["by_event_type"] == {event_type.replace("-", "_"): 1}
        assert [event["memory_id"] for event in report["events"]] == [visible]
        assert hidden not in json.dumps(report)
    elif operation == "verify":
        assert report["checked_events"] == 1
        assert report["valid"]
    else:
        assert report["deleted"] == 1
        assert storage.inspect_recall_utility(
            memory_id=hidden, event_type=event_type
        )["summary"]["total_events"] == 1
        assert storage.inspect_recall_utility(
            memory_id=visible, event_type="surfaced"
        )["summary"]["total_events"] == 1
