import sqlite3

import pytest

from llm_memory.server import app as server_app
from llm_memory.server.app import app


@pytest.mark.asyncio
async def test_root_endpoint(client):
    response = await client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "online"
    assert "version" in data
    assert "stats" in data
    assert "total_memories" in data
    assert "storage_backend" in data
    assert "embedding_provider" in data


@pytest.mark.asyncio
async def test_root_endpoint_filters_stats_by_repo_id(client):
    headers = {"X-API-KEY": "test_key"}
    await client.post(
        "/memories",
        json={"content": "Repo A memory", "repo_id": "repo-a"},
        headers=headers,
    )
    await client.post(
        "/memories",
        json={"content": "Repo B memory", "repo_id": "repo-b"},
        headers=headers,
    )

    response = await client.get("/?repo_id=repo-a")

    assert response.status_code == 200
    data = response.json()
    assert data["stats"]["total_memories"] == 1
    assert data["total_memories"] == 1
    assert data["stats"]["memories_by_layer"] == {"episodic": 1}


@pytest.mark.asyncio
async def test_healthz_is_unauthenticated(client):
    response = await client.get("/healthz")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "version" in data


@pytest.mark.asyncio
async def test_readyz_reports_runtime_readiness(client, tmp_path, monkeypatch):
    monkeypatch.setattr(server_app, "STATIC_DIR", tmp_path)

    response = await client.get("/readyz")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"
    assert data["storage_ready"] is True
    assert "storage_backend" in data
    assert "embedding_provider" in data
    assert "auth_enabled" in data
    assert "dashboard_static_available" in data


@pytest.mark.asyncio
async def test_readyz_returns_503_when_dashboard_static_is_missing(client, tmp_path, monkeypatch):
    monkeypatch.setattr(server_app, "STATIC_DIR", tmp_path / "missing")

    response = await client.get("/readyz")

    assert response.status_code == 503
    data = response.json()
    assert data["status"] == "not_ready"
    assert data["storage_ready"] is True
    assert data["dashboard_static_available"] is False


@pytest.mark.asyncio
async def test_readyz_returns_503_when_storage_check_fails(client, tmp_path, monkeypatch):
    monkeypatch.setattr(server_app, "STATIC_DIR", tmp_path)

    class FailingStorage:
        def get_stats(self):
            raise RuntimeError("database unavailable")

    previous_storage = app.state.storage
    app.state.storage = FailingStorage()
    try:
        response = await client.get("/readyz")
    finally:
        app.state.storage = previous_storage

    assert response.status_code == 503
    data = response.json()
    assert data["status"] == "not_ready"
    assert data["storage_ready"] is False
    assert data["storage_error"] == "RuntimeError"
    assert "database unavailable" not in str(data)


@pytest.mark.asyncio
async def test_favicon_head_does_not_error(client):
    response = await client.head("/favicon.ico")
    assert response.status_code == 200


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
@pytest.mark.parametrize(
    "payload",
    [
        {"content": "Bad layer", "layer": "not-a-layer"},
        {"content": "Bad importance", "importance": 1.5},
        {"content": "Bad importance", "importance": -0.1},
        {"content": "", "layer": "episodic"},
    ],
)
async def test_create_memory_rejects_invalid_payloads(client, payload):
    headers = {"X-API-KEY": "test_key"}

    response = await client.post("/memories", json=payload, headers=headers)

    assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"importance": 2},
        {"importance": -1},
        {"content": ""},
    ],
)
async def test_update_memory_rejects_invalid_payloads(client, payload):
    headers = {"X-API-KEY": "test_key"}
    create_response = await client.post(
        "/memories",
        json={"content": "Update validation target"},
        headers=headers,
    )
    assert create_response.status_code == 200
    mem_id = create_response.json()["id"]

    response = await client.patch(f"/memories/{mem_id}", json=payload, headers=headers)

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_memory_responses_use_persisted_accessed_at(client):
    headers = {"X-API-KEY": "test_key"}
    create_response = await client.post(
        "/memories",
        json={"content": "Timestamp target", "layer": "episodic"},
        headers=headers,
    )
    assert create_response.status_code == 200
    mem_id = create_response.json()["id"]

    with sqlite3.connect(app.state.storage.db_path) as conn:
        conn.execute(
            "UPDATE memories SET accessed_at = ? WHERE id = ?",
            ("2000-01-01 00:00:00", mem_id),
        )
        conn.commit()

    list_response = await client.get("/memories", headers=headers)
    assert list_response.status_code == 200
    listed = next(memory for memory in list_response.json() if memory["id"] == mem_id)
    assert listed["accessed_at"] == "2000-01-01T00:00:00"

    get_response = await client.get(f"/memories/{mem_id}", headers=headers)
    assert get_response.status_code == 200
    assert get_response.json()["accessed_at"] == "2000-01-01T00:00:00"


@pytest.mark.asyncio
async def test_list_memories_filters_by_layer_and_category(client):
    headers = {"X-API-KEY": "test_key"}
    await client.post(
        "/memories",
        json={"content": "Regular event", "layer": "episodic", "category": "note"},
        headers=headers,
    )
    await client.post(
        "/memories",
        json={"content": "Fragile auth warning", "layer": "semantic", "category": "fragile_area"},
        headers=headers,
    )
    await client.post(
        "/memories",
        json={"content": "Team convention", "layer": "semantic", "category": "convention"},
        headers=headers,
    )

    response = await client.get(
        "/memories?layer=semantic&category=fragile_area",
        headers=headers,
    )

    assert response.status_code == 200
    assert [memory["content"] for memory in response.json()] == ["Fragile auth warning"]


@pytest.mark.asyncio
async def test_intents_endpoint(client):
    headers = {"X-API-KEY": "test_key"}
    response = await client.get("/intents", headers=headers)
    assert response.status_code == 200
    assert isinstance(response.json(), list)


@pytest.mark.asyncio
async def test_intents_preserve_repo_id(client):
    headers = {"X-API-KEY": "test_key"}
    payload = {"description": "Scoped Docker/Codex work", "priority": 2, "repo_id": "repo-a"}

    create_response = await client.post("/intents", json=payload, headers=headers)
    assert create_response.status_code == 200
    assert create_response.json()["repo_id"] == "repo-a"

    list_response = await client.get("/intents?repo_id=repo-a", headers=headers)
    assert list_response.status_code == 200
    intents = list_response.json()
    assert any(intent["description"] == payload["description"] for intent in intents)
    assert all(intent["repo_id"] == "repo-a" for intent in intents)


@pytest.mark.asyncio
async def test_complete_intent_endpoint_marks_intent_inactive(client):
    headers = {"X-API-KEY": "test_key"}
    payload = {"description": "Ship remote done support", "priority": 2}

    create_response = await client.post("/intents", json=payload, headers=headers)
    assert create_response.status_code == 200
    intent_id = create_response.json()["id"]

    complete_response = await client.post(f"/intents/{intent_id}/complete", headers=headers)
    assert complete_response.status_code == 200
    assert complete_response.json() == {"status": "completed", "id": intent_id}

    list_response = await client.get("/intents", headers=headers)
    assert list_response.status_code == 200
    assert all(intent["id"] != intent_id for intent in list_response.json())


@pytest.mark.asyncio
async def test_complete_intent_endpoint_returns_404_for_missing_intent(client):
    headers = {"X-API-KEY": "test_key"}

    response = await client.post("/intents/missing/complete", headers=headers)

    assert response.status_code == 404
    assert response.json()["detail"] == "Intent not found"


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
    assert "relevance_score" in response.json()[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"query": "", "limit": 10},
        {"query": "target", "limit": 0},
        {"query": "target", "limit": 201},
        {"query": "target", "layers": ["not-a-layer"]},
    ],
)
async def test_recall_rejects_invalid_payloads(client, payload):
    headers = {"X-API-KEY": "test_key"}

    response = await client.post("/recall", json=payload, headers=headers)

    assert response.status_code == 422


def test_relationships_post_route_registered_once():
    matching_routes = [
        route
        for route in app.router.routes
        if route.path == "/relationships" and "POST" in (getattr(route, "methods", set()) or set())
    ]

    assert len(matching_routes) == 1


@pytest.mark.asyncio
async def test_relationships_endpoint_returns_created_contract(client):
    headers = {"X-API-KEY": "test_key"}
    source = (
        await client.post(
            "/memories",
            json={"content": "Source memory", "repo_id": "repo-a"},
            headers=headers,
        )
    ).json()["id"]
    target = (
        await client.post(
            "/memories",
            json={"content": "Target memory", "repo_id": "repo-a"},
            headers=headers,
        )
    ).json()["id"]

    response = await client.post(
        "/relationships",
        json={"source_id": source, "target_id": target, "relationship": "related"},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json()["status"] == "created"
    assert response.json()["id"]


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

    cross_response = await client.post(
        "/relationships",
        json={"source_id": mem_a, "target_id": mem_b, "relationship": "cross"},
        headers=headers,
    )
    assert cross_response.status_code == 400
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
