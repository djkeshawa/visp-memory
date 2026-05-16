import pytest


@pytest.mark.asyncio
async def test_root_endpoint(client):
    response = await client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "online"
    assert "version" in data
    assert "stats" in data
    assert "total_memories" in data

@pytest.mark.asyncio
async def test_memories_endpoint_protected(client):
    # Should fail without auth
    response = await client.get("/memories")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_memories_endpoint_with_api_key(client):
    headers = {"X-API-KEY": "test_key"}
    response = await client.get("/memories", headers=headers)
    assert response.status_code == 200
    assert isinstance(response.json(), list)


@pytest.mark.asyncio
async def test_create_memory_with_attribution(client):
    headers = {"X-API-KEY": "test_key"}
    payload = {
        "content": "Test memory with attribution",
        "layer": "episodic",
        "category": "test",
    }
    response = await client.post("/memories", json=payload, headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["content"] == payload["content"]

    # Verify memory exists and has author_id in metadata
    mem_id = data["id"]
    response = await client.get(f"/memories/{mem_id}", headers=headers)
    assert response.status_code == 200
    assert response.json()["metadata"]["author_id"] == "api_key_user"


@pytest.mark.asyncio
async def test_intents_endpoint(client):
    headers = {"X-API-KEY": "test_key"}
    response = await client.get("/intents", headers=headers)
    assert response.status_code == 200
    assert isinstance(response.json(), list)


@pytest.mark.asyncio
async def test_recall_endpoint(client):
    headers = {"X-API-KEY": "test_key"}
    # First create a memory to recall
    await client.post(
        "/memories",
        json={"content": "Recall target", "layer": "episodic"},
        headers=headers,
    )

    payload = {"query": "target", "limit": 10}
    response = await client.post("/recall", json=payload, headers=headers)
    assert response.status_code == 200
    assert len(response.json()) > 0
    assert response.json()[0]["content"] == "Recall target"
    assert "similarity" in response.json()[0]


@pytest.mark.asyncio
async def test_graph_endpoint_filters_relationships_by_repo(client):
    headers = {"X-API-KEY": "test_key"}
    mem_a = (
        await client.post(
            "/memories",
            json={"content": "Repo A memory", "repo_id": "repo-a"},
            headers=headers,
        )
    ).json()["id"]
    mem_b = (
        await client.post(
            "/memories",
            json={"content": "Repo B memory", "repo_id": "repo-b"},
            headers=headers,
        )
    ).json()["id"]
    mem_b2 = (
        await client.post(
            "/memories",
            json={"content": "Repo B related memory", "repo_id": "repo-b"},
            headers=headers,
        )
    ).json()["id"]

    await client.post(
        "/relationships",
        json={"source_id": mem_a, "target_id": mem_b, "relationship": "cross"},
        headers=headers,
    )
    await client.post(
        "/relationships",
        json={"source_id": mem_b, "target_id": mem_b2, "relationship": "same"},
        headers=headers,
    )

    response = await client.get("/graph?repo_id=repo-b", headers=headers)
    assert response.status_code == 200
    graph = response.json()
    assert {node["id"] for node in graph["nodes"]} == {mem_b, mem_b2}
    assert len(graph["links"]) == 1
    assert graph["links"][0]["label"] == "same"
