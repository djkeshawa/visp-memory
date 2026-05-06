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
        config.embedding.provider = "noop"
        config.server = ServerConfig(api_keys=["test_key"], auth_enabled=True)
        config.storage.api_key = "test_key"
        
        with mock.patch("llm_memory.server.auth.load_config", return_value=config):
            with TestClient(app) as c:
                yield c

def test_user_and_team_management(client):
    headers = {"X-API-KEY": "test_key"}
    
    # 1. Create Users
    resp_u1 = client.post("/teams/users", json={"username": "alice", "display_name": "Alice"}, headers=headers)
    assert resp_u1.status_code == 200
    user_id = resp_u1.json()["id"]
    
    # 2. Create Team
    resp_t1 = client.post("/teams", json={"name": "Alpha Team"}, headers=headers)
    assert resp_t1.status_code == 200
    team_id = resp_t1.json()["id"]
    
    # 3. Add Member to Team
    resp_m = client.post(f"/teams/{team_id}/members", json={"user_id": user_id}, headers=headers)
    assert resp_m.status_code == 200
    
    # 4. Verify Membership
    resp_ut = client.get(f"/teams/users/{user_id}/teams", headers=headers)
    assert resp_ut.status_code == 200
    assert len(resp_ut.json()) == 1
    assert resp_ut.json()[0]["name"] == "Alpha Team"

def test_team_attribution_and_access(client):
    headers = {"X-API-KEY": "test_key"}
    
    # Setup: User alice in Team Alpha. Team Gamma doesn't exist yet but we'll use IDs.
    client.post("/teams/users", json={"username": "alice", "id": "alice-id"}, headers=headers)
    client.post("/teams", json={"name": "Alpha", "id": "alpha-id"}, headers=headers)
    client.post("/teams/alpha-id/members", json={"user_id": "alice-id"}, headers=headers)
    
    # Register a repo and assign to team
    client.post("/repos", json={"name": "Project X", "id": "repo-x"}, headers=headers)
    
    # Manual update of repo team_id (or we could expose update repo API)
    # Actually, let's just check that we can fetch repos by team_id
    # Wait, the repo register endpoint already sets team_id from user context!
    
    # Since we are mocking auth, the user context's team_id is usually None unless we provide it.
    # But currently it's hardcoded to None in fake auth service for API key.
    
    # I'll check that we can list repos by team_id if we manually register one.
    client.post("/repos", json={"name": "Project Alpha", "id": "repo-alpha"}, headers=headers)
    
    # Get repos for team (None since auth user has no team in default mock)
    # Let's fix the list_repositories router to accept team_id param
    resp = client.get("/repos?team_id=alpha-id", headers=headers)
    # It should be empty because we didn't assign repo-alpha to alpha-id yet
    assert len(resp.json()) == 0
    
    # Let's register another repo with an explicit team_id? 
    # The current schema doesn't have team_id in RepositoryCreate, but I can add it or mock the user.
    
    # Actually, I'll just check that list_repositories(team_id=...) works by adding one manually in storage
    app.state.storage.store_repository({
        "name": "Manual Repo",
        "id": "repo-manual",
        "team_id": "alpha-id"
    })
    
    resp = client.get("/repos?team_id=alpha-id", headers=headers)
    assert len(resp.json()) == 1
    assert resp.json()[0]["id"] == "repo-manual"
