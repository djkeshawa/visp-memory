import pytest
import unittest.mock as mock
from fastapi import HTTPException
from jose import jwt
from llm_memory.server.auth import create_access_token, get_current_user, UserContext
from llm_memory.config import MemoryConfig, ServerConfig
import datetime

class MockRequest:
    def __init__(self, headers=None):
        self.headers = headers or {}

class MockAuth:
    def __init__(self, credentials):
        self.credentials = credentials

@pytest.fixture
def mock_config():
    config = MemoryConfig()
    config.server = ServerConfig(
        jwt_secret="test_secret",
        api_keys=["test_key"],
        jwt_expiry_hours=24
    )
    with mock.patch("llm_memory.server.auth.load_config", return_value=config):
        yield config

def test_create_access_token(mock_config):
    data = {"sub": "user123", "username": "testuser"}
    token = create_access_token(data)
    assert token is not None
    
    payload = jwt.decode(token, "test_secret", algorithms=["HS256"])
    assert payload["sub"] == "user123"
    assert payload["username"] == "testuser"
    assert "exp" in payload

@pytest.mark.asyncio
async def test_get_current_user_api_key(mock_config):
    request = MockRequest(headers={"X-API-KEY": "test_key"})
    user = await get_current_user(request, auth=None)
    assert user.username == "api_key"
    assert user.is_admin is True

@pytest.mark.asyncio
async def test_get_current_user_jwt(mock_config):
    token = create_access_token({"sub": "user456", "username": "jwtuser"})
    auth = MockAuth(credentials=token)
    request = MockRequest()
    
    user = await get_current_user(request, auth=auth)
    assert user.user_id == "user456"
    assert user.username == "jwtuser"

@pytest.mark.asyncio
async def test_get_current_user_invalid_jwt(mock_config):
    auth = MockAuth(credentials="invalid.token.here")
    request = MockRequest()
    
    with pytest.raises(HTTPException) as exc:
        await get_current_user(request, auth=auth)
    assert exc.value.status_code == 401

@pytest.mark.asyncio
async def test_get_current_user_no_auth(mock_config):
    request = MockRequest()
    mock_config.server.allow_anonymous = False
    
    with pytest.raises(HTTPException) as exc:
        await get_current_user(request, auth=None)
    assert exc.value.status_code == 401
