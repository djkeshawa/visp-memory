import pytest

from visp_memory.server.app import app


def project_token(*, scopes=None, repo_ids=None):
    account = app.state.auth_store.create_account(
        username="restricted-admin",
        password="correct-horse-battery-staple",
        role="admin",
        team_id="alpha",
    )
    _, token = app.state.auth_store.create_token(
        user_id=account["id"],
        name="restricted",
        scopes=scopes or ["project:read"],
        repo_ids=repo_ids if repo_ids is not None else ["allowed"],
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def repositories(client):
    storage = app.state.storage
    for repo_id, team in [("allowed", "alpha"), ("same-team", "alpha"), ("foreign", "beta")]:
        storage.store_repository({"id": repo_id, "name": repo_id, "team_id": team})
        storage.store_memory(
            f"{repo_id} private context",
            repo_id=repo_id,
            category="breaking_change",
            metadata={"team_id": team},
            auto_link=False,
        )
    storage.add_repo_dependency("allowed", "same-team", "depends_on")
    storage.add_repo_dependency("allowed", "foreign", "depends_on")


@pytest.mark.asyncio
@pytest.mark.parametrize("repo_id", ["same-team", "foreign"])
@pytest.mark.parametrize("suffix", ["", "/context", "/dependencies"])
async def test_repository_routes_enforce_token_allowlist(client, repositories, repo_id, suffix):
    response = await client.get(f"/repos/{repo_id}{suffix}", headers=project_token())
    assert response.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/repos", "/repos/scopes"])
async def test_repository_lists_enforce_token_allowlist(client, repositories, path):
    response = await client.get(path, headers=project_token())
    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == ["allowed"]


@pytest.mark.asyncio
async def test_context_filters_dependencies_and_record_tenancy(client, repositories):
    app.state.storage.store_memory(
        "foreign record in allowed repository",
        repo_id="allowed",
        category="breaking_change",
        metadata={"team_id": "beta"},
        auto_link=False,
    )
    headers = project_token()
    response = await client.get("/repos/allowed/context", headers=headers)
    assert response.status_code == 200
    assert response.json()["monitored_repos"] == ["allowed"]
    assert [row["content"] for row in response.json()["breaking_changes"]] == [
        "allowed private context"
    ]
    dependencies = await client.get("/repos/allowed/dependencies", headers=headers)
    assert dependencies.json() == []


@pytest.mark.asyncio
async def test_admin_account_without_admin_scope_still_obeys_team(client, repositories):
    headers = project_token(repo_ids=[])
    response = await client.get("/repos/foreign/context", headers=headers)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_admin_scope_does_not_override_repository_allowlist(client, repositories):
    headers = project_token(scopes=["*"], repo_ids=["allowed"])
    response = await client.get("/repos/foreign/purge-preview", headers=headers)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_project_write_token_cannot_register_outside_allowlist(client):
    response = await client.post(
        "/repos",
        json={"id": "foreign", "name": "Foreign"},
        headers=project_token(scopes=["project:write"]),
    )
    assert response.status_code == 404
    assert app.state.storage.get_repository("foreign") is None


@pytest.mark.asyncio
async def test_repository_context_forwards_time_and_scope_constraints(client, repositories):
    storage = app.state.storage
    storage.store_memory(
        "expired warning", repo_id="allowed", tags=["warning"],
        metadata={"team_id": "alpha", "valid_to": "2020-01-01T00:00:00Z"}, auto_link=False,
    )
    scoped_id = storage.store_memory(
        "production deployment warning", repo_id="allowed", tags=["warning"],
        metadata={"team_id": "alpha", "environment": "prod", "task_type": "deploy",
                  "valid_from": "2025-01-01T00:00:00Z"}, auto_link=False,
    )
    headers = project_token()
    unscoped = await client.get("/repos/allowed/context", headers=headers)
    assert unscoped.json()["warnings"] == []
    scoped = await client.get(
        "/repos/allowed/context", headers=headers,
        params={"environment": "prod", "task_type": "deploy", "as_of": "2026-01-01T00:00:00Z"},
    )
    assert [row["id"] for row in scoped.json()["warnings"]] == [scoped_id]
    invalid = await client.get(
        "/repos/allowed/context", headers=headers, params={"environment": " "},
    )
    assert invalid.status_code == 422
