import asyncio

import pytest

from llm_memory.server.app import app
from llm_memory.server.auth import UserContext, get_current_user


def set_current_user(user: UserContext):
    async def override_current_user():
        return user

    app.dependency_overrides[get_current_user] = override_current_user


def clear_current_user():
    app.dependency_overrides.pop(get_current_user, None)


async def get_without_hanging(client, path: str):
    return await asyncio.wait_for(client.get(path), timeout=1)


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
async def test_duplicate_user_and_team_ids_return_conflict_without_overwrite(client):
    headers = {"X-API-KEY": "test_key"}

    user = await client.post(
        "/teams/users",
        json={"id": "alice-id", "username": "alice", "display_name": "Alice"},
        headers=headers,
    )
    assert user.status_code == 200

    duplicate_user = await client.post(
        "/teams/users",
        json={"id": "alice-id", "username": "renamed", "display_name": "Replacement"},
        headers=headers,
    )
    assert duplicate_user.status_code == 409

    stored_user = await client.get("/teams/users/alice-id", headers=headers)
    assert stored_user.status_code == 200
    assert stored_user.json()["display_name"] == "Alice"

    team = await client.post(
        "/teams",
        json={"id": "team-alpha", "name": "Alpha", "description": "Original"},
        headers=headers,
    )
    assert team.status_code == 200

    duplicate_team = await client.post(
        "/teams",
        json={"id": "team-alpha", "name": "Renamed", "description": "Replacement"},
        headers=headers,
    )
    assert duplicate_team.status_code == 409

    stored_team = await client.get("/teams/team-alpha", headers=headers)
    assert stored_team.status_code == 200
    assert stored_team.json()["description"] == "Original"


@pytest.mark.asyncio
async def test_non_admin_user_read_does_not_hang_with_current_user_override(client):
    app.state.storage.store_user({"id": "alice-id", "username": "alice"})

    set_current_user(
        UserContext(
            user_id="alice-id",
            username="alice",
            team_id="team-alpha",
            is_admin=False,
        )
    )
    try:
        own_user = await get_without_hanging(client, "/teams/users/alice-id")
        assert own_user.status_code == 200
        assert own_user.json()["id"] == "alice-id"
    finally:
        clear_current_user()


@pytest.mark.asyncio
async def test_non_admin_team_reads_are_scoped_to_current_user_and_team(client):
    app.state.storage.store_user({"id": "alice-id", "username": "alice"})
    app.state.storage.store_user({"id": "bob-id", "username": "bob"})
    app.state.storage.store_team({"id": "team-alpha", "name": "Alpha"})
    app.state.storage.store_team({"id": "team-beta", "name": "Beta"})
    app.state.storage.add_team_member("team-alpha", "alice-id")
    app.state.storage.add_team_member("team-beta", "alice-id")
    app.state.storage.add_team_member("team-beta", "bob-id")

    set_current_user(
        UserContext(
            user_id="alice-id",
            username="alice",
            team_id="team-alpha",
            is_admin=False,
        )
    )
    try:
        own_user = await get_without_hanging(client, "/teams/users/alice-id")
        assert own_user.status_code == 200
        assert own_user.json()["id"] == "alice-id"

        other_user = await get_without_hanging(client, "/teams/users/bob-id")
        assert other_user.status_code == 404

        own_team = await get_without_hanging(client, "/teams/team-alpha")
        assert own_team.status_code == 200
        assert own_team.json()["id"] == "team-alpha"

        other_team = await get_without_hanging(client, "/teams/team-beta")
        assert other_team.status_code == 404

        own_teams = await get_without_hanging(client, "/teams/users/alice-id/teams")
        assert own_teams.status_code == 200
        assert [team["id"] for team in own_teams.json()] == ["team-alpha"]

        other_user_teams = await get_without_hanging(client, "/teams/users/bob-id/teams")
        assert other_user_teams.status_code == 403
    finally:
        clear_current_user()


@pytest.mark.asyncio
async def test_non_admin_cannot_manage_users_teams_or_memberships(client):
    app.state.storage.store_user({"id": "alice-id", "username": "alice"})
    app.state.storage.store_user({"id": "bob-id", "username": "bob"})
    app.state.storage.store_team({"id": "team-alpha", "name": "Alpha"})

    set_current_user(
        UserContext(
            user_id="alice-id",
            username="alice",
            team_id="team-alpha",
            is_admin=False,
        )
    )
    try:
        create_user = await client.post(
            "/teams/users",
            json={"id": "mallory-id", "username": "mallory"},
        )
        assert create_user.status_code == 403

        create_team = await client.post(
            "/teams",
            json={"id": "team-beta", "name": "Beta"},
        )
        assert create_team.status_code == 403

        add_member = await client.post(
            "/teams/team-alpha/members",
            json={"user_id": "bob-id"},
        )
        assert add_member.status_code == 403
        assert app.state.storage.get_user_teams("bob-id") == []
    finally:
        clear_current_user()


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


@pytest.mark.asyncio
async def test_non_admin_memory_routes_are_scoped_to_current_team(client):
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
    alpha_memory_id = app.state.storage.store_memory(
        "Alpha private memory",
        repo_id="repo-alpha",
        metadata={"team_id": "team-alpha"},
    )
    beta_memory_id = app.state.storage.store_memory(
        "Beta private memory",
        repo_id="repo-beta",
        metadata={"team_id": "team-beta"},
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
        own_memories = await client.get("/memories?repo_id=repo-alpha")
        assert own_memories.status_code == 200
        assert [memory["id"] for memory in own_memories.json()] == [alpha_memory_id]

        other_repo_memories = await client.get("/memories?repo_id=repo-beta")
        assert other_repo_memories.status_code == 404

        leaked_recall = await client.post(
            "/recall",
            json={"query": "private", "limit": 10},
        )
        assert leaked_recall.status_code == 200
        assert [memory["id"] for memory in leaked_recall.json()] == [alpha_memory_id]

        blocked_create = await client.post(
            "/memories",
            json={"content": "Cross-team write", "repo_id": "repo-beta"},
        )
        assert blocked_create.status_code == 404

        blocked_get = await client.get(f"/memories/{beta_memory_id}")
        assert blocked_get.status_code == 404

        blocked_update = await client.patch(
            f"/memories/{beta_memory_id}",
            json={"content": "Changed by wrong team"},
        )
        assert blocked_update.status_code == 404

        blocked_delete = await client.delete(f"/memories/{beta_memory_id}")
        assert blocked_delete.status_code == 404
        assert app.state.storage.get_memory(beta_memory_id)["content"] == "Beta private memory"
    finally:
        clear_current_user()


@pytest.mark.asyncio
async def test_non_admin_intent_routes_are_scoped_to_current_team(client):
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
    alpha_intent_id = app.state.storage.set_intent(
        "Alpha ship work",
        repo_id="repo-alpha",
        context={"team_id": "team-alpha"},
    )
    beta_intent_id = app.state.storage.set_intent(
        "Beta ship work",
        repo_id="repo-beta",
        context={"team_id": "team-beta"},
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
        own_intents = await client.get("/intents")
        assert own_intents.status_code == 200
        assert [intent["id"] for intent in own_intents.json()] == [alpha_intent_id]

        other_repo_intents = await client.get("/intents?repo_id=repo-beta")
        assert other_repo_intents.status_code == 404

        blocked_create = await client.post(
            "/intents",
            json={"description": "Cross-team goal", "repo_id": "repo-beta"},
        )
        assert blocked_create.status_code == 404

        blocked_complete = await client.post(f"/intents/{beta_intent_id}/complete")
        assert blocked_complete.status_code == 404
        assert any(
            intent["id"] == beta_intent_id
            for intent in app.state.storage.get_active_intents(repo_id="repo-beta")
        )

        complete_own = await client.post(f"/intents/{alpha_intent_id}/complete")
        assert complete_own.status_code == 200
        assert complete_own.json() == {"status": "completed", "id": alpha_intent_id}
    finally:
        clear_current_user()


@pytest.mark.asyncio
async def test_non_admin_relationship_routes_are_scoped_to_current_team(client):
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
    alpha_source_id = app.state.storage.store_memory(
        "Alpha source",
        repo_id="repo-alpha",
        metadata={"team_id": "team-alpha"},
    )
    alpha_target_id = app.state.storage.store_memory(
        "Alpha target",
        repo_id="repo-alpha",
        metadata={"team_id": "team-alpha"},
    )
    beta_source_id = app.state.storage.store_memory(
        "Beta source",
        repo_id="repo-beta",
        metadata={"team_id": "team-beta"},
    )
    beta_target_id = app.state.storage.store_memory(
        "Beta target",
        repo_id="repo-beta",
        metadata={"team_id": "team-beta"},
    )
    alpha_relationship_id = app.state.storage.add_relationship(
        alpha_source_id,
        alpha_target_id,
        "related_to",
    )
    app.state.storage.add_relationship(beta_source_id, beta_target_id, "related_to")

    set_current_user(
        UserContext(
            user_id="alice",
            username="alice",
            team_id="team-alpha",
            is_admin=False,
        )
    )
    try:
        relationships = await client.get("/relationships")
        assert relationships.status_code == 200
        assert [relationship["id"] for relationship in relationships.json()] == [
            alpha_relationship_id
        ]

        other_repo_relationships = await client.get("/relationships?repo_id=repo-beta")
        assert other_repo_relationships.status_code == 404

        blocked_cross_team = await client.post(
            "/relationships",
            json={
                "source_id": alpha_source_id,
                "target_id": beta_source_id,
                "relationship": "related_to",
            },
        )
        assert blocked_cross_team.status_code == 404

        allowed_same_team = await client.post(
            "/relationships",
            json={
                "source_id": alpha_source_id,
                "target_id": alpha_target_id,
                "relationship": "supports",
            },
        )
        assert allowed_same_team.status_code == 200
    finally:
        clear_current_user()
