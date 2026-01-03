import unittest.mock as mock

import jwt
import pytest
from fastapi import HTTPException

from visp_memory.config import MemoryConfig, ServerConfig
from visp_memory.server.auth import create_access_token, get_current_user


class MockRequest:
    def __init__(self, headers=None):
        self.headers = headers or {}
        self.cookies = {}


class MockAuth:
    def __init__(self, credentials):
        self.credentials = credentials


@pytest.fixture
def mock_config():
    config = MemoryConfig()
    config.server = ServerConfig(
        jwt_secret="test_secret_at_least_32_bytes_long",
        api_keys=["test_key"],
        jwt_expiry_hours=24,
    )
    with mock.patch("visp_memory.server.auth.load_config", return_value=config):
        yield config


def test_create_access_token(mock_config):
    data = {"sub": "user123", "username": "testuser"}
    token = create_access_token(data)
    assert token is not None

    payload = jwt.decode(token, "test_secret_at_least_32_bytes_long", algorithms=["HS256"])
    assert payload["sub"] == "user123"
    assert payload["username"] == "testuser"
    assert "exp" in payload


def test_create_access_token_requires_jwt_secret(mock_config):
    mock_config.server.jwt_secret = ""

    with pytest.raises(ValueError, match="VISP_MEMORY_JWT_SECRET"):
        create_access_token({"sub": "user123"})


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
async def test_get_current_user_jwt_requires_configured_secret(mock_config):
    mock_config.server.jwt_secret = ""
    auth = MockAuth(credentials="token")
    request = MockRequest()

    with pytest.raises(HTTPException) as exc:
        await get_current_user(request, auth=auth)
    assert exc.value.status_code == 401
    assert exc.value.detail == "JWT authentication is not configured"


@pytest.mark.asyncio
async def test_get_current_user_api_key_without_jwt_secret(mock_config):
    mock_config.server.jwt_secret = ""
    request = MockRequest(headers={"X-API-KEY": "test_key"})

    user = await get_current_user(request, auth=None)

    assert user.username == "api_key"
    assert user.is_admin is True


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


@pytest.mark.asyncio
async def test_get_current_user_auth_disabled(mock_config):
    request = MockRequest()
    mock_config.server.auth_enabled = False

    user = await get_current_user(request, auth=None)

    assert user.username == "local"
    assert user.is_admin is True


@pytest.mark.asyncio
async def test_password_login_session_csrf_and_logout(client):
    from visp_memory.server.app import app

    app.state.auth_store.create_account(
        username="owner",
        password="correct-horse-battery-staple",
        role="admin",
    )
    rejected = await client.post(
        "/auth/login", json={"username": "owner", "password": "wrong-password"}
    )
    assert rejected.status_code == 401

    login = await client.post(
        "/auth/login",
        json={"username": "owner", "password": "correct-horse-battery-staple"},
    )
    assert login.status_code == 200
    assert "HttpOnly" in login.headers["set-cookie"]
    csrf_token = login.json()["csrf_token"]

    current = await client.get("/auth/me")
    assert current.status_code == 200
    assert current.json()["user"]["username"] == "owner"

    missing_csrf = await client.post("/auth/logout")
    assert missing_csrf.status_code == 403
    logged_out = await client.post("/auth/logout", headers={"X-CSRF-Token": csrf_token})
    assert logged_out.status_code == 200
    assert (await client.get("/auth/me")).status_code == 401


@pytest.mark.asyncio
async def test_personal_access_token_is_scoped_and_revocable(client):
    from visp_memory.server.app import app

    app.state.auth_store.create_account(
        username="token-owner",
        password="correct-horse-battery-staple",
        role="admin",
    )
    login = await client.post(
        "/auth/login",
        json={"username": "token-owner", "password": "correct-horse-battery-staple"},
    )
    csrf_token = login.json()["csrf_token"]
    created = await client.post(
        "/auth/tokens",
        headers={"X-CSRF-Token": csrf_token},
        json={
            "name": "Codex",
            "scopes": ["memory:read"],
            "repo_ids": ["allowed-repo"],
        },
    )
    assert created.status_code == 201
    token = created.json()["token"]
    token_id = created.json()["id"]
    assert token.startswith("llmm_")

    pat_headers = {"Authorization": f"Bearer {token}"}
    assert (await client.get("/auth/me", headers=pat_headers)).status_code == 200
    assert (await client.post("/memories", headers=pat_headers, json={})).status_code == 403
    assert (await client.get("/repos", headers=pat_headers)).status_code == 403

    revoked = await client.delete(
        f"/auth/tokens/{token_id}", headers={"X-CSRF-Token": csrf_token}
    )
    assert revoked.status_code == 200
    assert (await client.get("/auth/me", headers=pat_headers)).status_code == 401
