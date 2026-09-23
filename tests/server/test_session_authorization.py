from unittest.mock import Mock

import pytest

from visp_memory.server.app import app
from visp_memory.server.auth import UserContext, get_current_user


def _as_user(user: UserContext) -> None:
    async def current_user() -> UserContext:
        return user

    app.dependency_overrides[get_current_user] = current_user


@pytest.fixture(autouse=True)
def _clear_user_override():
    yield
    app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
async def test_session_creation_requires_repository_scope(client):
    response = await client.post("/sessions", headers={"X-API-KEY": "test_key"})

    assert response.status_code == 400
    assert response.json()["detail"] == "repo_id is required"


@pytest.mark.asyncio
async def test_only_session_owner_can_complete(client):
    app.state.storage.store_repository(
        {"id": "repo-a", "name": "Repo A", "team_id": "team-alpha"}
    )
    alice = UserContext(
        user_id="alice", username="alice", team_id="team-alpha", auth_type="session"
    )
    bob = UserContext(
        user_id="bob", username="bob", team_id="team-alpha", auth_type="session"
    )
    _as_user(alice)
    started = await client.post("/sessions", json={"repo_id": "repo-a"})
    session_id = started.json()["id"]

    _as_user(bob)
    response = await client.post(
        f"/sessions/{session_id}/complete",
        json={"summary": "Not mine", "memory_ids": []},
    )

    assert response.status_code == 404
    assert app.state.storage.get_session(session_id)["ended_at"] is None


@pytest.mark.asyncio
async def test_completion_rejects_invisible_or_foreign_memory_before_evaluation(client):
    app.state.storage.store_repository(
        {"id": "repo-a", "name": "Repo A", "team_id": "team-alpha"}
    )
    app.state.storage.store_repository(
        {"id": "repo-b", "name": "Repo B", "team_id": "team-beta"}
    )
    alice = UserContext(
        user_id="alice", username="alice", team_id="team-alpha", auth_type="session"
    )
    _as_user(alice)
    started = await client.post("/sessions", json={"repo_id": "repo-a"})
    session_id = started.json()["id"]
    foreign_id = app.state.storage.store_memory(
        "Other team's memory",
        repo_id="repo-b",
        metadata={"team_id": "team-beta"},
        auto_link=False,
    )
    evaluator = Mock(wraps=app.state.intent_evaluator)
    app.state.intent_evaluator = evaluator

    response = await client.post(
        f"/sessions/{session_id}/complete",
        json={"summary": "Mixed", "memory_ids": [foreign_id]},
    )

    assert response.status_code == 404
    assert app.state.storage.get_session(session_id)["ended_at"] is None
    evaluator.evaluate_repository.assert_not_called()


@pytest.mark.asyncio
async def test_missing_and_duplicate_completion_have_distinct_statuses(client):
    app.state.storage.store_repository(
        {"id": "repo-a", "name": "Repo A", "team_id": "team-alpha"}
    )
    _as_user(
        UserContext(
            user_id="alice", username="alice", team_id="team-alpha", auth_type="session"
        )
    )

    missing = await client.post(
        "/sessions/missing/complete",
        json={"summary": "", "memory_ids": []},
    )
    started = await client.post("/sessions", json={"repo_id": "repo-a"})
    session_id = started.json()["id"]
    first = await client.post(
        f"/sessions/{session_id}/complete",
        json={"summary": "Done", "memory_ids": []},
    )
    duplicate = await client.post(
        f"/sessions/{session_id}/complete",
        json={"summary": "Again", "memory_ids": []},
    )

    assert missing.status_code == 404
    assert first.status_code == 200
    assert duplicate.status_code == 409


@pytest.mark.asyncio
async def test_legacy_unbound_session_cannot_be_completed(client):
    legacy_session_id = app.state.storage.start_session()
    _as_user(
        UserContext(
            user_id="alice", username="alice", team_id="team-alpha", auth_type="session"
        )
    )

    response = await client.post(
        f"/sessions/{legacy_session_id}/complete",
        json={"summary": "Legacy", "memory_ids": []},
    )

    assert response.status_code == 404
    assert app.state.storage.get_session(legacy_session_id)["ended_at"] is None


@pytest.mark.asyncio
async def test_completion_evaluates_only_visible_intents(client):
    app.state.storage.store_repository(
        {"id": "repo-a", "name": "Repo A", "team_id": "team-alpha"}
    )
    alice = UserContext(
        user_id="alice", username="alice", team_id="team-alpha", auth_type="session"
    )
    _as_user(alice)
    visible_id = app.state.storage.set_intent(
        "Alpha intent",
        repo_id="repo-a",
        context={"team_id": "team-alpha"},
    )
    hidden_id = app.state.storage.set_intent(
        "Beta intent",
        repo_id="repo-a",
        context={"team_id": "team-beta"},
    )
    started = await client.post("/sessions", json={"repo_id": "repo-a"})

    response = await client.post(
        f"/sessions/{started.json()['id']}/complete",
        json={"summary": "Finished work", "memory_ids": []},
    )

    assert response.status_code == 200
    evaluated_ids = {
        item["intent_id"] for item in response.json()["intent_evaluations"]
    }
    assert evaluated_ids == {visible_id}
    assert hidden_id not in evaluated_ids
