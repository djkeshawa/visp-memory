from unittest import mock

import pytest

from visp_memory import MemoryConfig
from visp_memory.core.trust import Provenance, provenance_tag
from visp_memory.server.app import app
from visp_memory.server.auth import UserContext, get_current_user


def _set_current_user(user):
    async def override_current_user():
        return user

    app.dependency_overrides[get_current_user] = override_current_user


def _clear_current_user():
    app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
async def test_api_key_admin_recall_refuses_absent_repository_scope(client):
    app.state.storage.store_memory("repo a global leak", repo_id="repo-a", auto_link=False)
    app.state.storage.store_memory("repo b global leak", repo_id="repo-b", auto_link=False)

    with mock.patch(
        "visp_memory.server.routers.memories.load_config",
        return_value=MemoryConfig(repo_id=None),
    ):
        response = await client.post(
            "/recall",
            json={"query": "global leak", "min_score": 0.0},
            headers={"X-API-KEY": "test_key"},
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "repo_id is required"


@pytest.mark.asyncio
async def test_http_memory_schema_normalizes_scope_and_recall_enforces_it(client):
    headers = {"X-API-KEY": "test_key"}
    created = await client.post(
        "/memories",
        json={
            "content": "production deployment scope",
            "repo_id": "repo-a",
            "environment": ["prod", "prod"],
            "task_type": ["test", "deploy", "deploy"],
        },
        headers=headers,
    )
    assert created.status_code == 200
    payload = created.json()
    assert payload["environment"] == ["prod"]
    assert payload["task_type"] == ["deploy", "test"]

    matched = await client.post(
        "/recall",
        json={
            "query": "production deployment scope",
            "repo_id": "repo-a",
            "environment": "prod",
            "task_type": "deploy",
            "min_score": 0.0,
        },
        headers=headers,
    )
    wrong_environment = await client.post(
        "/recall",
        json={
            "query": "production deployment scope",
            "repo_id": "repo-a",
            "environment": "dev",
            "task_type": "deploy",
            "min_score": 0.0,
        },
        headers=headers,
    )

    assert [item["id"] for item in matched.json()] == [payload["id"]]
    assert wrong_environment.json() == []


@pytest.mark.asyncio
async def test_http_context_compile_enforces_environment_and_task_scope(client):
    storage = app.state.storage
    matched = storage.store_memory(
        "context scoped production deployment",
        layer="semantic",
        repo_id="repo-a",
        tags=[provenance_tag(Provenance.DERIVED)],
        metadata={"environment": "prod", "task_type": "deploy"},
        auto_link=False,
    )
    storage.store_memory(
        "context scoped development deployment",
        layer="semantic",
        repo_id="repo-a",
        tags=[provenance_tag(Provenance.DERIVED)],
        metadata={"environment": "dev", "task_type": "deploy"},
        auto_link=False,
    )

    response = await client.post(
        "/context/compile",
        json={
            "query": "context scoped deployment",
            "repo_id": "repo-a",
            "environment": "prod",
            "task_type": "deploy",
            "token_budget": 2000,
        },
        headers={"X-API-KEY": "test_key"},
    )

    assert response.status_code == 200
    assert {item["id"] for item in response.json()["items"]} == {matched}


@pytest.mark.asyncio
async def test_non_admin_metadata_update_cannot_change_memory_scope(client):
    storage = app.state.storage
    storage.store_repository(
        {"id": "repo-a", "name": "Repo A", "team_id": "team-alpha"}
    )
    scoped = storage.store_memory(
        "scope cannot be rewritten",
        repo_id="repo-a",
        metadata={
            "team_id": "team-alpha",
            "environment": ["prod"],
            "task_type": ["deploy"],
        },
        auto_link=False,
    )
    unscoped = storage.store_memory(
        "scope cannot be introduced",
        repo_id="repo-a",
        metadata={"team_id": "team-alpha"},
        auto_link=False,
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
        changed = await client.patch(
            f"/memories/{scoped}",
            json={"metadata": {"environment": ["dev"], "task_type": ["review"]}},
        )
        introduced = await client.patch(
            f"/memories/{unscoped}",
            json={"metadata": {"environment": ["prod"], "task_type": ["deploy"]}},
        )
    finally:
        _clear_current_user()

    assert changed.status_code == 200
    assert introduced.status_code == 200
    assert storage.get_memory(scoped)["metadata"] == {
        "team_id": "team-alpha",
        "environment": ["prod"],
        "task_type": ["deploy"],
    }
    assert storage.get_memory(unscoped)["metadata"] == {"team_id": "team-alpha"}
