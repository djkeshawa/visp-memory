import pytest


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
        },
        headers=headers,
    )

    resp = await client.get("/repos/app-repo/context", headers=headers)
    assert resp.status_code == 200
    data = resp.json()

    warnings = [w["content"] for w in data["warnings"]]
    assert "Lib API Deprecated" in warnings
    assert "lib-repo" in data["monitored_repos"]
