import json

import pytest

from visp_memory.server.app import app
from visp_memory.server.auth import UserContext, get_current_user


def _set_current_user(user: UserContext):
    async def override_current_user():
        return user

    app.dependency_overrides[get_current_user] = override_current_user


def _clear_current_user():
    app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
async def test_memory_intelligence_report_filters_team_scoped_rows_in_json_and_text(client):
    repo_id = "same-unregistered-report-repo"
    storage = app.state.storage
    alpha_memory_id = storage.store_memory(
        "Alpha report memory",
        repo_id=repo_id,
        importance=0.9,
        metadata={"team_id": "team-alpha"},
        auto_link=False,
    )
    beta_memory_id = storage.store_memory(
        "Beta report secret",
        repo_id=repo_id,
        importance=0.9,
        metadata={"team_id": "team-beta"},
        auto_link=False,
    )
    storage.add_relationship(alpha_memory_id, beta_memory_id, "supports")
    storage.set_intent(
        "Alpha report intent",
        repo_id=repo_id,
        context={"team_id": "team-alpha"},
    )
    storage.set_intent(
        "Beta report intent secret",
        repo_id=repo_id,
        context={"team_id": "team-beta"},
    )

    _set_current_user(
        UserContext(
            user_id="alice",
            username="alice",
            team_id="team-alpha",
            is_admin=False,
        )
    )
    try:
        json_response = await client.get(
            "/reports/memory-intelligence",
            params={"repo_id": repo_id},
        )
        text_response = await client.get(
            "/reports/memory-intelligence/text",
            params={"repo_id": repo_id},
        )
    finally:
        _clear_current_user()

    assert json_response.status_code == 200
    assert text_response.status_code == 200
    report = json_response.json()
    assert report["summary"]["total_memories"] == 1
    assert report["summary"]["total_relationships"] == 0
    assert report["summary"]["active_intents"] == 1
    assert "Beta report secret" not in json.dumps(report)
    assert "Beta report intent secret" not in json.dumps(report)
    assert "Beta report secret" not in text_response.text
    assert "Beta report intent secret" not in text_response.text


@pytest.mark.asyncio
async def test_admin_account_pat_without_admin_scope_stays_record_scoped(client):
    repo_id = "pat-report-repo"
    storage = app.state.storage
    alpha_id = storage.store_memory(
        "PAT-visible report memory",
        repo_id=repo_id,
        importance=0.9,
        metadata={"team_id": "team-alpha"},
        auto_link=False,
    )
    beta_id = storage.store_memory(
        "PAT-hidden report memory",
        repo_id=repo_id,
        importance=0.9,
        metadata={"team_id": "team-beta"},
        auto_link=False,
    )
    storage.add_relationship(
        alpha_id,
        beta_id,
        "supports",
        evidence={"reason": "PAT-hidden relationship reason"},
    )
    account = app.state.auth_store.create_account(
        username="report-admin",
        password="correct-horse-battery-staple",
        role="admin",
        team_id="team-alpha",
    )
    _, token = app.state.auth_store.create_token(
        user_id=account["id"],
        name="project-read-only",
        scopes=["project:read"],
        repo_ids=[repo_id],
    )

    response = await client.get(
        "/reports/memory-intelligence",
        params={"repo_id": repo_id},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    report = response.json()
    serialized = json.dumps(report)
    assert report["summary"]["total_memories"] == 1
    assert report["summary"]["total_relationships"] == 0
    assert alpha_id in serialized
    assert beta_id not in serialized
    assert "PAT-hidden report memory" not in serialized
    assert "PAT-hidden relationship reason" not in serialized
