import pytest
from fastapi.testclient import TestClient
from llm_memory.server.app import app
from llm_memory.config import MemoryConfig, ServerConfig
import unittest.mock as mock
from llm_memory.core.storage import LocalStorage
import tempfile
from pathlib import Path

@pytest.fixture
def client():
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = LocalStorage(Path(tmpdir))
        app.state.storage = storage
        
        config = MemoryConfig()
        config.server = ServerConfig(api_keys=["test_key"], auth_enabled=True)
        config.storage.api_key = "test_key"
        
        with mock.patch("llm_memory.server.auth.load_config", return_value=config):
            with TestClient(app) as c:
                yield c

def test_repo_registration_and_isolation(client):
    headers = {"X-API-KEY": "test_key"}
    
    # 1. Register two repositories
    client.post("/repos", json={"name": "Project A", "id": "repo-a"}, headers=headers)
    client.post("/repos", json={"name": "Project B", "id": "repo-b"}, headers=headers)
    
    # 2. Add memories to different repos
    client.post("/memories", json={"content": "Mem A", "repo_id": "repo-a"}, headers=headers)
    client.post("/memories", json={"content": "Mem B", "repo_id": "repo-b"}, headers=headers)
    
    # 3. Verify isolation in list
    resp_a = client.get("/memories?repo_id=repo-a", headers=headers)
    assert len(resp_a.json()) == 1
    assert resp_a.json()[0]["content"] == "Mem A"
    
    resp_b = client.get("/memories?repo_id=repo-b", headers=headers)
    assert len(resp_b.json()) == 1
    assert resp_b.json()[0]["content"] == "Mem B"

def test_repository_dependencies(client):
    headers = {"X-API-KEY": "test_key"}
    
    # Register repos
    client.post("/repos", json={"name": "Lib", "id": "lib-repo"}, headers=headers)
    client.post("/repos", json={"name": "App", "id": "app-repo"}, headers=headers)
    
    # Add dependency: App depends on Lib
    dep_payload = {
        "target_repo_id": "lib-repo",
        "dependency_type": "depends_on",
        "version": "1.0.0"
    }
    client.post("/repos/app-repo/dependencies", json=dep_payload, headers=headers)
    
    # Verify dependency
    resp = client.get("/repos/app-repo/dependencies", headers=headers)
    assert len(resp.json()) == 1
    assert resp.json()[0]["target_repo_id"] == "lib-repo"

def test_cross_repo_context(client):
    headers = {"X-API-KEY": "test_key"}
    
    # Setup: App depends on Lib. Lib has a warning.
    client.post("/repos", json={"name": "Lib", "id": "lib-repo"}, headers=headers)
    client.post("/repos", json={"name": "App", "id": "app-repo"}, headers=headers)
    client.post("/repos/app-repo/dependencies", json={"target_repo_id": "lib-repo"}, headers=headers)
    
    # Add warning to Lib
    client.post("/memories", json={
        "content": "Lib API Deprecated", 
        "repo_id": "lib-repo",
        "category": "warning"
    }, headers=headers)
    
    # Get context for App
    resp = client.get("/repos/app-repo/context", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    
    # Should see the warning from Lib in App's context
    warnings = [w["content"] for w in data["warnings"]]
    assert "Lib API Deprecated" in warnings
    assert "lib-repo" in data["monitored_repos"]
