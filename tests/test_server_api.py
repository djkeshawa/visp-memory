import tempfile
import unittest.mock as mock
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from llm_memory.config import MemoryConfig, ServerConfig
from llm_memory.core.storage import LocalStorage
from llm_memory.server.app import app


@pytest.fixture
def client():
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a fresh storage for each test
        storage = LocalStorage(Path(tmpdir))
        app.state.storage = storage

        # Configure test app
        config = MemoryConfig()
        config.embedding.provider = "noop"
        config.server = ServerConfig(
            api_keys=["test_key"],
            auth_enabled=True,
            allow_anonymous=False
        )
        config.storage.api_key = "test_key"

        with mock.patch("llm_memory.server.auth.load_config", return_value=config):
            with mock.patch("llm_memory.server.routers.memories.load_config", return_value=config):
                with mock.patch("llm_memory.server.routers.intents.load_config", return_value=config):
                    with TestClient(app) as c:
                        yield c

def test_root_endpoint(client):
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "online"
    assert "version" in data
    assert "stats" in data
    assert "total_memories" in data

def test_memories_endpoint_protected(client):
    # Should fail without auth
    response = client.get("/memories")
    assert response.status_code == 401

def test_memories_endpoint_with_api_key(client):
    headers = {"X-API-KEY": "test_key"}
    response = client.get("/memories", headers=headers)
    assert response.status_code == 200
    assert isinstance(response.json(), list)

def test_create_memory_with_attribution(client):
    headers = {"X-API-KEY": "test_key"}
    payload = {
        "content": "Test memory with attribution",
        "layer": "episodic",
        "category": "test"
    }
    response = client.post("/memories", json=payload, headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["content"] == payload["content"]

    # Verify memory exists and has author_id in metadata
    mem_id = data["id"]
    response = client.get(f"/memories/{mem_id}", headers=headers)
    assert response.status_code == 200
    assert response.json()["metadata"]["author_id"] == "api_key_user"

def test_intents_endpoint(client):
    headers = {"X-API-KEY": "test_key"}
    response = client.get("/intents", headers=headers)
    assert response.status_code == 200
    assert isinstance(response.json(), list)

def test_recall_endpoint(client):
    headers = {"X-API-KEY": "test_key"}
    # First create a memory to recall
    client.post("/memories", json={"content": "Recall target", "layer": "episodic"}, headers=headers)

    payload = {"query": "target", "limit": 10}
    response = client.post("/recall", json=payload, headers=headers)
    assert response.status_code == 200
    assert len(response.json()) > 0
    assert response.json()[0]["content"] == "Recall target"
    assert "similarity" in response.json()[0]

def test_graph_endpoint_filters_relationships_by_repo(client):
    headers = {"X-API-KEY": "test_key"}
    mem_a = client.post(
        "/memories",
        json={"content": "Repo A memory", "repo_id": "repo-a"},
        headers=headers,
    ).json()["id"]
    mem_b = client.post(
        "/memories",
        json={"content": "Repo B memory", "repo_id": "repo-b"},
        headers=headers,
    ).json()["id"]
    mem_b2 = client.post(
        "/memories",
        json={"content": "Repo B related memory", "repo_id": "repo-b"},
        headers=headers,
    ).json()["id"]

    client.post(
        "/relationships",
        json={"source_id": mem_a, "target_id": mem_b, "relationship": "cross"},
        headers=headers,
    )
    client.post(
        "/relationships",
        json={"source_id": mem_b, "target_id": mem_b2, "relationship": "same"},
        headers=headers,
    )

    response = client.get("/graph?repo_id=repo-b", headers=headers)
    assert response.status_code == 200
    graph = response.json()
    assert {node["id"] for node in graph["nodes"]} == {mem_b, mem_b2}
    assert len(graph["links"]) == 1
    assert graph["links"][0]["label"] == "same"
