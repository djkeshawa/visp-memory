import pytest

from llm_memory.server.app import app
from llm_memory.server.auth import UserContext, get_current_user


def set_current_user(user: UserContext):
    app.dependency_overrides[get_current_user] = lambda: user


def clear_current_user():
    app.dependency_overrides.pop(get_current_user, None)


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
async def test_team_membership_requires_existing_team_and_user(client):
    headers = {"X-API-KEY": "test_key"}

    missing_team = await client.post(
        "/teams/missing-team/members",
        json={"user_id": "missing-user"},
        headers=headers,
    )
    assert missing_team.status_code == 404

    await client.post("/teams", json={"name": "Alpha Team", "id": "alpha-id"}, headers=headers)

    missing_user = await client.post(
        "/teams/alpha-id/members",
        json={"user_id": "missing-user"},
        headers=headers,
    )
    assert missing_user.status_code == 404

    teams = await client.get("/teams/users/missing-user/teams", headers=headers)
    assert teams.json() == []


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


@pytest.mark.asyncio
async def test_non_admin_repository_access_is_scoped_to_user_team(client):
    app.state.storage.store_repository(
        {
            "name": "Alpha Repo",
            "id": "repo-alpha",
            "team_id": "team-alpha",
        }
    )
    app.state.storage.store_repository(
        {
            "name": "Beta Repo",
            "id": "repo-beta",
            "team_id": "team-beta",
        }
    )

    set_current_user(
        UserContext(
            user_id="alice",
            username="alice",
            team_id="team-alpha",
            is_admin=False,
        )
    )
    try:
        own_repos = await client.get("/repos")
        assert own_repos.status_code == 200
        assert [repo["id"] for repo in own_repos.json()] == ["repo-alpha"]

        other_team_repos = await client.get("/repos?team_id=team-beta")
        assert other_team_repos.status_code == 403

        own_repo = await client.get("/repos/repo-alpha")
        assert own_repo.status_code == 200
        assert own_repo.json()["id"] == "repo-alpha"

        other_repo = await client.get("/repos/repo-beta")
        assert other_repo.status_code == 404
    finally:
        clear_current_user()


@pytest.mark.asyncio
async def test_non_admin_repository_dependency_access_excludes_other_teams(client):
    app.state.storage.store_repository(
        {
            "name": "Alpha App",
            "id": "repo-alpha-app",
            "team_id": "team-alpha",
        }
    )
    app.state.storage.store_repository(
        {
            "name": "Alpha Lib",
            "id": "repo-alpha-lib",
            "team_id": "team-alpha",
        }
    )
    app.state.storage.store_repository(
        {
            "name": "Beta Lib",
            "id": "repo-beta-lib",
            "team_id": "team-beta",
        }
    )
    app.state.storage.add_repo_dependency("repo-alpha-app", "repo-alpha-lib", "depends_on")
    app.state.storage.add_repo_dependency("repo-alpha-app", "repo-beta-lib", "depends_on")

    set_current_user(
        UserContext(
            user_id="alice",
            username="alice",
            team_id="team-alpha",
            is_admin=False,
        )
    )
    try:
        deps = await client.get("/repos/repo-alpha-app/dependencies")
        assert deps.status_code == 200
        assert [dep["target_repo_id"] for dep in deps.json()] == ["repo-alpha-lib"]

        blocked_add = await client.post(
            "/repos/repo-alpha-app/dependencies",
            json={"target_repo_id": "repo-beta-lib"},
        )
        assert blocked_add.status_code == 404

        blocked_source = await client.get("/repos/repo-beta-lib/dependencies")
        assert blocked_source.status_code == 404
    finally:
        clear_current_user()


@pytest.mark.asyncio
async def test_non_admin_cross_repo_context_does_not_include_other_team_dependencies(client):
    app.state.storage.store_repository(
        {
            "name": "Alpha App",
            "id": "context-alpha-app",
            "team_id": "team-alpha",
        }
    )
    app.state.storage.store_repository(
        {
            "name": "Alpha Lib",
            "id": "context-alpha-lib",
            "team_id": "team-alpha",
        }
    )
    app.state.storage.store_repository(
        {
            "name": "Beta Lib",
            "id": "context-beta-lib",
            "team_id": "team-beta",
        }
    )
    app.state.storage.add_repo_dependency("context-alpha-app", "context-alpha-lib", "depends_on")
    app.state.storage.add_repo_dependency("context-alpha-app", "context-beta-lib", "depends_on")
    app.state.storage.store_memory(
        "Alpha warning",
        repo_id="context-alpha-lib",
        category="warning",
    )
    app.state.storage.store_memory(
        "Beta warning",
        repo_id="context-beta-lib",
        category="warning",
    )

    set_current_user(
        UserContext(
            user_id="alice",
            username="alice",
            team_id="team-alpha",
            is_admin=False,
        )
    )
    try:
        context = await client.get("/repos/context-alpha-app/context")
        assert context.status_code == 200
        data = context.json()
        assert data["monitored_repos"] == ["context-alpha-app", "context-alpha-lib"]
        assert [warning["content"] for warning in data["warnings"]] == ["Alpha warning"]

        blocked_context = await client.get("/repos/context-beta-lib/context")
        assert blocked_context.status_code == 404
    finally:
        clear_current_user()
