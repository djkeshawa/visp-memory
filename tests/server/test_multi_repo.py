import pytest

from visp_memory.server.app import app


@pytest.mark.asyncio
async def test_repo_registration_and_isolation(client):
    headers = {"X-API-KEY": "test_key"}

    await client.post("/repos", json={"name": "Project A", "id": "repo-a"}, headers=headers)
    await client.post("/repos", json={"name": "Project B", "id": "repo-b"}, headers=headers)

    await client.post("/memories", json={"content": "Mem A", "repo_id": "repo-a"}, headers=headers)
    await client.post("/memories", json={"content": "Mem B", "repo_id": "repo-b"}, headers=headers)

    resp_a = await client.get("/memories?repo_id=repo-a", headers=headers)
    assert len(resp_a.json()) == 1
    assert resp_a.json()[0]["content"] == "Mem A"

    resp_b = await client.get("/memories?repo_id=repo-b", headers=headers)
    assert len(resp_b.json()) == 1
    assert resp_b.json()[0]["content"] == "Mem B"


@pytest.mark.asyncio
async def test_repo_registration_rejects_duplicate_generated_id(client):
    headers = {"X-API-KEY": "test_key"}

    first = await client.post(
        "/repos",
        json={"name": "My Repo", "description": "Original"},
        headers=headers,
    )
    assert first.status_code == 200

    second = await client.post(
        "/repos",
        json={"name": "my-repo", "description": "Replacement"},
        headers=headers,
    )
    assert second.status_code == 409

    repo = await client.get("/repos/my-repo", headers=headers)
    assert repo.status_code == 200
    assert repo.json()["description"] == "Original"


@pytest.mark.asyncio
async def test_repository_dependencies(client):
    headers = {"X-API-KEY": "test_key"}

    await client.post("/repos", json={"name": "Lib", "id": "lib-repo"}, headers=headers)
    await client.post("/repos", json={"name": "App", "id": "app-repo"}, headers=headers)

    dep_payload = {
        "target_repo_id": "lib-repo",
        "dependency_type": "depends_on",
        "version": "1.0.0",
    }
    await client.post("/repos/app-repo/dependencies", json=dep_payload, headers=headers)

    resp = await client.get("/repos/app-repo/dependencies", headers=headers)
    assert len(resp.json()) == 1
    assert resp.json()[0]["target_repo_id"] == "lib-repo"


@pytest.mark.asyncio
async def test_repository_dependencies_require_existing_repositories(client):
    headers = {"X-API-KEY": "test_key"}

    missing_source = await client.post(
        "/repos/missing-app/dependencies",
        json={"target_repo_id": "missing-lib"},
        headers=headers,
    )
    assert missing_source.status_code == 404

    await client.post("/repos", json={"name": "App", "id": "app-repo"}, headers=headers)

    missing_target = await client.post(
        "/repos/app-repo/dependencies",
        json={"target_repo_id": "missing-lib"},
        headers=headers,
    )
    assert missing_target.status_code == 404

    deps = await client.get("/repos/app-repo/dependencies", headers=headers)
    assert deps.json() == []


@pytest.mark.asyncio
async def test_cross_repo_context(client):
    headers = {"X-API-KEY": "test_key"}

    await client.post("/repos", json={"name": "Lib", "id": "lib-repo"}, headers=headers)
    await client.post("/repos", json={"name": "App", "id": "app-repo"}, headers=headers)
    await client.post(
        "/repos/app-repo/dependencies",
        json={"target_repo_id": "lib-repo"},
        headers=headers,
    )

    await client.post(
        "/memories",
        json={
            "content": "Lib API Deprecated",
            "repo_id": "lib-repo",
            "category": "warning",
            "tags": ["warning"],
        },
        headers=headers,
    )

    resp = await client.get("/repos/app-repo/context", headers=headers)
    assert resp.status_code == 200
    data = resp.json()

    warnings = [w["content"] for w in data["warnings"]]
    assert "Lib API Deprecated" in warnings
    assert "lib-repo" in data["monitored_repos"]


@pytest.mark.asyncio
async def test_repository_archive_restore_and_confirmed_purge(client):
    headers = {"X-API-KEY": "test_key"}
    await client.post(
        "/repos", json={"name": "Lifecycle", "id": "lifecycle-repo"}, headers=headers
    )
    memory = await client.post(
        "/memories",
        json={"content": "Project memory", "repo_id": "lifecycle-repo"},
        headers=headers,
    )

    archived = await client.post("/repos/lifecycle-repo/archive", headers=headers)
    assert archived.status_code == 200
    scopes = await client.get("/repos/scopes", headers=headers)
    assert "lifecycle-repo" not in {item["id"] for item in scopes.json()}
    blocked_write = await client.post(
        "/memories",
        json={"content": "Blocked", "repo_id": "lifecycle-repo"},
        headers=headers,
    )
    assert blocked_write.status_code == 409

    archived_list = await client.get("/repos?include_archived=true", headers=headers)
    assert any(
        repo["id"] == "lifecycle-repo" and repo["status"] == "archived"
        for repo in archived_list.json()
    )
    restored = await client.post("/repos/lifecycle-repo/restore", headers=headers)
    assert restored.status_code == 200

    preview = await client.get("/repos/lifecycle-repo/purge-preview", headers=headers)
    assert preview.json()["memories"] == 1
    rejected = await client.delete(
        "/repos/lifecycle-repo?confirmation=wrong", headers=headers
    )
    assert rejected.status_code == 400
    purged = await client.delete(
        "/repos/lifecycle-repo?confirmation=lifecycle-repo", headers=headers
    )
    assert purged.status_code == 200
    assert purged.json()["backup"].endswith(".json")
    assert (await client.get("/repos/lifecycle-repo", headers=headers)).status_code == 404
    memory_lookup = await client.get(f"/memories/{memory.json()['id']}", headers=headers)
    assert memory_lookup.status_code == 404


@pytest.mark.asyncio
async def test_reflection_materialization_requires_a_writable_repository(client):
    headers = {"X-API-KEY": "test_key"}
    await client.post(
        "/repos",
        json={"name": "Reflection", "id": "reflection-repo"},
        headers=headers,
    )
    first = await client.post(
        "/memories", json={"content": "Evidence one", "repo_id": "reflection-repo"}, headers=headers
    )
    second = await client.post(
        "/memories", json={"content": "Evidence two", "repo_id": "reflection-repo"}, headers=headers
    )
    assert first.status_code == second.status_code == 200
    await client.post("/repos/reflection-repo/archive", headers=headers)

    response = await client.post(
        "/ai/reflections",
        json={
            "repo_id": "reflection-repo",
            "title": "Archived runbook",
            "evidence_ids": [first.json()["id"], second.json()["id"]],
            "reviewed": True,
        },
        headers=headers,
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "Repository is archived and does not accept new writes"


@pytest.mark.asyncio
async def test_repository_purge_reports_conflict_and_retains_scope_on_vector_failure(
    client, monkeypatch
):
    headers = {"X-API-KEY": "test_key"}
    await client.post(
        "/repos",
        json={"name": "Vector Failure", "id": "vector-repo"},
        headers=headers,
    )
    memory = await client.post(
        "/memories",
        json={"content": "Keep this if vector cleanup fails", "repo_id": "vector-repo"},
        headers=headers,
    )

    class FailingCollection:
        def delete(self, *, ids):
            raise RuntimeError("vector unavailable")

    monkeypatch.setattr(app.state.storage, "_get_collection", lambda _layer: FailingCollection())

    purged = await client.delete(
        "/repos/vector-repo?confirmation=vector-repo", headers=headers
    )

    assert purged.status_code == 409
    assert purged.json()["detail"]["status"] == "incomplete"
    assert (await client.get("/repos/vector-repo", headers=headers)).status_code == 200
    assert (
        await client.get(f"/memories/{memory.json()['id']}", headers=headers)
    ).status_code == 200
