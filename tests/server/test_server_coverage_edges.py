"""Behavior-level coverage for server authentication, AI, and app boundaries.

These tests intentionally exercise failure and authorization outcomes through the
same public contracts that callers use.  The existing API tests cover happy-path
memory writes well; this file fills in the less common branches that protect the
server when credentials, providers, storage, or graph boundaries fail.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from starlette.requests import Request
from starlette.responses import Response

from visp_memory.config import MemoryConfig
from visp_memory.core.model_router import ModelUnavailableError
from visp_memory.core.trust import Provenance, provenance_tag
from visp_memory.server import app as server_app
from visp_memory.server.app import app
from visp_memory.server.auth import UserContext, get_current_user
from visp_memory.server.auth_store import AuthStore


def _set_current_user(user: UserContext) -> None:
    async def current_user() -> UserContext:
        return user

    app.dependency_overrides[get_current_user] = current_user


@pytest.fixture(autouse=True)
def _clear_current_user_override():
    yield
    app.dependency_overrides.pop(get_current_user, None)


def _stored_memory(repo_id: str, content: str, *, metadata=None, tags=None) -> str:
    """Store a searchable, trusted memory without going through another route."""
    evidence_id = app.state.storage.store_evidence(content, repo_id=repo_id)
    return app.state.storage.store_memory(
        content,
        layer="semantic",
        category="fact",
        repo_id=repo_id,
        evidence_ids=[evidence_id],
        metadata=metadata or {},
        tags=tags or [provenance_tag(Provenance.DERIVED)],
        auto_link=False,
    )


def _http_scope(path: str, *, method: str = "GET", request_id: str = "") -> Request:
    headers = []
    if request_id:
        headers.append((b"x-request-id", request_id.encode()))
    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": headers,
            "client": ("127.0.0.1", 8080),
            "server": ("testserver", 80),
        }
    )


def test_auth_store_validates_accounts_and_bootstraps_only_once(tmp_path):
    store = AuthStore(tmp_path / "auth.db")
    password = "correct-horse-battery-staple"

    account = store.bootstrap_admin("", password)

    assert account["username"] == "admin"
    assert account["role"] == "admin"
    assert store.bootstrap_admin("second", password) is None

    with pytest.raises(ValueError, match="Username"):
        store.create_account(username="ab", password=password)
    with pytest.raises(ValueError, match="Password"):
        store.create_account(username="valid-user", password="short")
    with pytest.raises(ValueError, match="Role"):
        store.create_account(username="valid-user", password=password, role="owner")
    with pytest.raises(ValueError, match="already exists"):
        store.create_account(username="ADMIN", password=password)
    with pytest.raises(ValueError, match="already exists"):
        store.create_account(username="other-user", password=password, user_id=account["id"])


def test_auth_store_updates_accounts_and_revokes_sessions_on_password_change(tmp_path):
    store = AuthStore(tmp_path / "auth.db")
    account = store.create_account(
        username="account-owner",
        password="correct-horse-battery-staple",
        team_id="team-a",
    )
    session, _ = store.create_session(account["id"])

    updated = store.update_account(
        account["id"],
        display_name="Account Owner",
        email="owner@example.test",
        role="admin",
        team_id="team-b",
        enabled=False,
    )
    assert updated["display_name"] == "Account Owner"
    assert updated["email"] == "owner@example.test"
    assert updated["role"] == "admin"
    assert updated["team_id"] == "team-b"
    assert updated["enabled"] is False
    assert store.authenticate_password("account-owner", "correct-horse-battery-staple") is None
    assert store.update_account(account["id"], ignored="value")["id"] == account["id"]
    assert store.update_account("missing", display_name="Nobody") is None

    with pytest.raises(ValueError, match="Role"):
        store.update_account(account["id"], role="owner")
    with pytest.raises(ValueError, match="Password"):
        store.set_password(account["id"], "short")
    assert store.set_password("missing", "correct-horse-battery-staple") is False
    assert store.set_password(account["id"], "new-correct-horse-battery-staple") is True
    assert store.authenticate_session(session) is None

    disabled_session, _ = store.create_session(account["id"])
    assert store.authenticate_session(disabled_session) is None
    assert [item["username"] for item in store.list_accounts()] == ["account-owner"]


def test_auth_store_rehashes_a_valid_password_when_policy_requires_it(tmp_path):
    store = AuthStore(tmp_path / "auth.db")
    account = store.create_account(
        username="rehash-user", password="correct-horse-battery-staple"
    )
    with store._db() as connection:
        old_hash = connection.execute(
            "SELECT password_hash FROM auth_accounts WHERE id = ?", (account["id"],)
        ).fetchone()[0]
    password_hasher = Mock(wraps=store.passwords)
    password_hasher.check_needs_rehash.return_value = True
    store.passwords = password_hasher

    authenticated = store.authenticate_password(
        "REHASH-USER", "correct-horse-battery-staple"
    )

    assert authenticated["last_login_at"] is not None
    with store._db() as connection:
        password_hash = connection.execute(
            "SELECT password_hash FROM auth_accounts WHERE id = ?", (account["id"],)
        ).fetchone()[0]
    assert password_hash != old_hash


def test_auth_store_expires_and_removes_invalid_or_idle_sessions(tmp_path):
    store = AuthStore(tmp_path / "auth.db")
    account = store.create_account(
        username="session-user", password="correct-horse-battery-staple"
    )

    expired, _ = store.create_session(account["id"])
    with store._db() as connection:
        connection.execute(
            "UPDATE auth_sessions SET expires_at = ? WHERE token_hash = ?",
            ("2000-01-01T00:00:00+00:00", store._hash_secret(expired)),
        )
        connection.commit()
    assert store.authenticate_session(expired) is None

    malformed, _ = store.create_session(account["id"])
    with store._db() as connection:
        connection.execute(
            "UPDATE auth_sessions SET last_seen_at = ? WHERE token_hash = ?",
            ("not-a-date", store._hash_secret(malformed)),
        )
        connection.commit()
    assert store.authenticate_session(malformed) is None

    idle, _ = store.create_session(account["id"])
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    with store._db() as connection:
        connection.execute(
            "UPDATE auth_sessions SET last_seen_at = ? WHERE token_hash = ?",
            (old, store._hash_secret(idle)),
        )
        connection.commit()
    assert store.authenticate_session(idle, idle_hours=1) is None


def test_auth_store_token_listing_scope_and_rejection_states(tmp_path):
    store = AuthStore(tmp_path / "auth.db")
    owner = store.create_account(
        username="token-owner", password="correct-horse-battery-staple"
    )
    other = store.create_account(
        username="token-other", password="correct-horse-battery-staple"
    )
    record, token = store.create_token(
        user_id=owner["id"],
        name="  scoped token  ",
        scopes=["memory:write", "memory:read", "memory:read"],
        repo_ids=["repo-b", "repo-a", "repo-a"],
    )

    assert record["name"] == "scoped token"
    assert record["scopes"] == ["memory:read", "memory:write"]
    assert record["repo_ids"] == ["repo-a", "repo-b"]
    assert store.get_token(record["id"], user_id=other["id"]) is None
    assert store.list_tokens(owner["id"])[0]["id"] == record["id"]
    assert store.authenticate_token("not-a-personal-access-token") is None
    assert store.authenticate_token(token)["repo_ids"] == ["repo-a", "repo-b"]

    expired_record, expired_token = store.create_token(
        user_id=owner["id"],
        name="expired",
        scopes=[],
        repo_ids=[],
        expires_at="2000-01-01T00:00:00+00:00",
    )
    assert store.authenticate_token(expired_token) is None

    disabled_record, disabled_token = store.create_token(
        user_id=owner["id"], name="disabled", scopes=[], repo_ids=[]
    )
    store.update_account(owner["id"], enabled=False)
    assert store.authenticate_token(disabled_token) is None
    assert store.revoke_token(disabled_record["id"], user_id=other["id"]) is False
    assert store.revoke_token(disabled_record["id"], user_id=owner["id"]) is True
    assert store.revoke_token(disabled_record["id"], user_id=owner["id"]) is False
    assert store.get_token(expired_record["id"]) is not None


@pytest.mark.asyncio
async def test_authentication_status_and_admin_account_management(client, monkeypatch):
    config = MemoryConfig()
    config.server.auth_enabled = True
    monkeypatch.setattr(
        "visp_memory.server.routers.authentication.load_config", lambda: config
    )

    status_response = await client.get("/auth/status")
    assert status_response.json() == {"auth_enabled": True, "setup_required": True}

    headers = {"X-API-KEY": "test_key"}
    create = await client.post(
        "/auth/users",
        json={
            "username": "managed-user",
            "password": "correct-horse-battery-staple",
            "email": "before@example.test",
            "role": "user",
            "team_id": "team-a",
        },
        headers=headers,
    )
    assert create.status_code == 201
    user_id = create.json()["id"]

    duplicate = await client.post(
        "/auth/users",
        json={"username": "managed-user", "password": "correct-horse-battery-staple"},
        headers=headers,
    )
    assert duplicate.status_code == 409

    listed = await client.get("/auth/users", headers=headers)
    assert listed.status_code == 200
    assert any(item["id"] == user_id for item in listed.json())

    updated = await client.patch(
        f"/auth/users/{user_id}",
        json={"display_name": "Managed", "enabled": False, "role": "admin"},
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["display_name"] == "Managed"
    assert updated.json()["enabled"] is False
    assert updated.json()["role"] == "admin"

    missing_update = await client.patch(
        "/auth/users/missing", json={"display_name": "Nobody"}, headers=headers
    )
    assert missing_update.status_code == 404

    reset = await client.post(
        f"/auth/users/{user_id}/password",
        json={"password": "new-correct-horse-battery-staple"},
        headers=headers,
    )
    assert reset.status_code == 200
    assert reset.json() == {"status": "password_reset"}
    missing_reset = await client.post(
        "/auth/users/missing/password",
        json={"password": "new-correct-horse-battery-staple"},
        headers=headers,
    )
    assert missing_reset.status_code == 404

    config.server.auth_enabled = False
    setup_disabled = await client.get("/auth/status")
    assert setup_disabled.json() == {"auth_enabled": False, "setup_required": False}


@pytest.mark.asyncio
async def test_authentication_token_contract_validates_scope_expiry_and_revoke(client):
    api_key_headers = {"X-API-KEY": "test_key"}

    listed = await client.get("/auth/tokens", headers=api_key_headers)
    assert listed.status_code == 200
    assert listed.json() == []

    account = app.state.auth_store.create_account(
        username="token-api-owner", password="correct-horse-battery-staple"
    )
    login = await client.post(
        "/auth/login",
        json={"username": "token-api-owner", "password": "correct-horse-battery-staple"},
    )
    assert login.status_code == 200
    headers = {"X-CSRF-Token": login.json()["csrf_token"]}
    assert app.state.auth_store.get_account(account["id"])["username"] == "token-api-owner"

    invalid_scope = await client.post(
        "/auth/tokens",
        json={"name": "bad", "scopes": ["memory:read", "not-a-scope"]},
        headers=headers,
    )
    assert invalid_scope.status_code == 400
    assert "not-a-scope" in invalid_scope.json()["detail"]

    expired = await client.post(
        "/auth/tokens",
        json={"name": "expired", "expires_at": "2000-01-01T00:00:00Z"},
        headers=headers,
    )
    assert expired.status_code == 400

    created = await client.post(
        "/auth/tokens",
        json={
            "name": "working",
            "scopes": ["memory:read", "memory:read"],
            "repo_ids": ["repo-a"],
        },
        headers=headers,
    )
    assert created.status_code == 201
    token_id = created.json()["id"]
    assert created.json()["token"].startswith("llmm_")

    missing = await client.delete("/auth/tokens/missing", headers=headers)
    assert missing.status_code == 404
    revoked = await client.delete(f"/auth/tokens/{token_id}", headers=headers)
    assert revoked.status_code == 200
    assert revoked.json() == {"status": "revoked", "id": token_id}


@pytest.mark.asyncio
async def test_authentication_token_admin_scope_is_rejected_for_ordinary_user(client):
    app.state.auth_store.create_account(
        username="backed-user",
        password="correct-horse-battery-staple",
        user_id="user",
    )
    _set_current_user(
        UserContext(user_id="user", username="user", auth_type="session", is_admin=False)
    )

    response = await client.post(
        "/auth/tokens",
        json={"name": "admin-token", "scopes": ["admin"]},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Admin scope is restricted"


@pytest.mark.asyncio
async def test_authentication_token_creation_rejects_unbacked_api_key_principal(client):
    response = await client.post(
        "/auth/tokens",
        json={"name": "api-key-token", "scopes": ["memory:read"]},
        headers={"X-API-KEY": "test_key"},
    )

    assert response.status_code == 403
    assert response.json() == {
        "detail": "A persistent account is required to create personal access tokens"
    }


class _FakeModelRouter:
    def __init__(self, result=None, error=None):
        self.configured = True
        self.result = result
        self.error = error
        self.calls = []

    def complete(self, task, prompt, *, system_prompt=None):
        self.calls.append((task, prompt, system_prompt))
        if self.error:
            raise self.error
        return self.result

    def status(self):
        return {"provider": "fake", "configured": True}


def _ai_config() -> MemoryConfig:
    config = MemoryConfig()
    config.repo_id = "repo-a"
    return config


@pytest.mark.asyncio
async def test_ai_ask_generates_from_scoped_citations_and_preserves_prompt(client, monkeypatch):
    memory_id = _stored_memory("repo-a", "The deployment uses a blue green rollout.")
    fake = _FakeModelRouter(
        result={"text": f"Use [{memory_id}]", "provider": "fake", "model": "test-model"}
    )
    app.state.model_router = fake
    monkeypatch.setattr("visp_memory.server.routers.ai.load_config", _ai_config)

    response = await client.post(
        "/ai/ask",
        json={"query": "blue green rollout", "repo_id": "repo-a"},
        headers={"X-API-KEY": "test_key"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["mode"] == "generated"
    assert data["provider_status"] == "available"
    assert data["provider"] == "fake"
    assert data["model"] == "test-model"
    assert data["citations"][0]["memory_id"] == memory_id
    assert fake.calls[0][0] == "answer"
    assert memory_id in fake.calls[0][1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [ModelUnavailableError("down"), ValueError("bad"), RuntimeError("boom")],
)
async def test_ai_ask_falls_back_to_retrieval_when_model_fails(client, monkeypatch, error):
    _stored_memory("repo-a", "Fallback answer evidence")
    app.state.model_router = _FakeModelRouter(error=error)
    monkeypatch.setattr("visp_memory.server.routers.ai.load_config", _ai_config)

    response = await client.post(
        "/ai/ask",
        json={"query": "Fallback answer evidence", "repo_id": "repo-a"},
        headers={"X-API-KEY": "test_key"},
    )

    assert response.status_code == 200
    assert response.json()["mode"] == "retrieval_only"
    assert response.json()["provider_status"] == "failed"


@pytest.mark.asyncio
async def test_ai_admin_routing_and_model_test_report_provider_outcomes(client):
    app.state.model_router = _FakeModelRouter(
        result={"text": "ready", "provider": "fake", "model": "test-model"}
    )
    headers = {"X-API-KEY": "test_key"}

    routing = await client.get("/ai/routing", headers=headers)
    assert routing.status_code == 200
    assert routing.json() == {"provider": "fake", "configured": True}

    tested = await client.post("/ai/test", headers=headers)
    assert tested.status_code == 200
    assert tested.json() == {
        "status": "connected",
        "text": "ready",
        "provider": "fake",
        "model": "test-model",
    }

    app.state.model_router = _FakeModelRouter(error=ModelUnavailableError("provider offline"))
    unavailable = await client.post("/ai/test", headers=headers)
    assert unavailable.status_code == 409
    assert unavailable.json()["detail"] == "provider offline"


@pytest.mark.asyncio
async def test_ai_reflection_preview_filters_hidden_evidence_for_team_user(client, monkeypatch):
    repo_id = "reflection-coverage-repo"
    app.state.storage.store_repository(
        {"id": repo_id, "name": "Reflection", "team_id": "team-alpha"}
    )
    visible_tag = [provenance_tag(Provenance.DERIVED), "visible-group"]
    hidden_tag = [provenance_tag(Provenance.DERIVED), "hidden-group"]
    visible_ids = [
        _stored_memory(
            repo_id,
            f"Visible evidence {index}",
            metadata={"team_id": "team-alpha"},
            tags=visible_tag,
        )
        for index in range(2)
    ]
    hidden_ids = [
        _stored_memory(
            repo_id,
            f"Hidden evidence {index}",
            metadata={"team_id": "team-beta"},
            tags=hidden_tag,
        )
        for index in range(2)
    ]
    _set_current_user(
        UserContext(user_id="alice", username="alice", team_id="team-alpha", auth_type="session")
    )

    monkeypatch.setattr("visp_memory.server.routers.ai.load_config", _ai_config)
    response = await client.get(
        "/ai/reflections",
        params={"repo_id": repo_id, "min_evidence": 1},
    )

    assert response.status_code == 200
    proposals = response.json()
    assert len(proposals) == 1
    assert set(proposals[0]["evidence_ids"]) == set(visible_ids)
    assert not set(hidden_ids).intersection(proposals[0]["evidence_ids"])


@pytest.mark.asyncio
async def test_ai_reflection_materialization_enforces_review_evidence_and_engine_errors(
    client, monkeypatch
):
    repo_id = "materialize-coverage-repo"
    first = _stored_memory(repo_id, "First materialization evidence")
    second = _stored_memory(repo_id, "Second materialization evidence")
    headers = {"X-API-KEY": "test_key"}

    unreviewed = await client.post(
        "/ai/reflections",
        json={"repo_id": repo_id, "title": "Runbook", "evidence_ids": [first, second]},
        headers=headers,
    )
    assert unreviewed.status_code == 409

    missing = await client.post(
        "/ai/reflections",
        json={
            "repo_id": repo_id,
            "title": "Runbook",
            "evidence_ids": [first, "missing"],
            "reviewed": True,
        },
        headers=headers,
    )
    assert missing.status_code == 404

    class SuccessfulEngine:
        def __init__(self, storage, model_router):
            assert storage is app.state.storage
            assert model_router is app.state.model_router

        def materialize(self, **kwargs):
            assert kwargs["repo_id"] == repo_id
            assert kwargs["evidence_ids"] == [first, second]
            return {"id": "reflection-result", "metadata": {"safe": True}}

    monkeypatch.setattr("visp_memory.server.routers.ai.ReflectionEngine", SuccessfulEngine)
    materialized = await client.post(
        "/ai/reflections",
        json={
            "repo_id": repo_id,
            "title": "Runbook",
            "evidence_ids": [first, second],
            "reviewed": True,
        },
        headers=headers,
    )
    assert materialized.status_code == 200
    assert materialized.json()["id"] == "reflection-result"

    class FailingEngine(SuccessfulEngine):
        def materialize(self, **kwargs):
            raise ValueError("evidence cannot be materialized")

    monkeypatch.setattr("visp_memory.server.routers.ai.ReflectionEngine", FailingEngine)
    failed = await client.post(
        "/ai/reflections",
        json={
            "repo_id": repo_id,
            "title": "Runbook",
            "evidence_ids": [first, second],
            "reviewed": True,
        },
        headers=headers,
    )
    assert failed.status_code == 400
    assert failed.json()["detail"] == "evidence cannot be materialized"


@pytest.mark.asyncio
async def test_request_context_middleware_preserves_ids_and_sanitizes_failures():
    request = _http_scope("/healthz", request_id="request-123")

    async def successful_call(_request):
        return Response(status_code=204)

    response = await server_app.request_context_middleware(request, successful_call)
    assert response.status_code == 204
    assert response.headers["X-Request-ID"] == "request-123"

    failing_request = _http_scope("/failure")

    async def failing_call(_request):
        raise RuntimeError("private database password")

    failed = await server_app.request_context_middleware(failing_request, failing_call)
    assert failed.status_code == 500
    assert failed.headers["X-Request-ID"]
    assert failed.body == (
        b'{"detail":"Internal server error","request_id":"'
        + failed.headers["X-Request-ID"].encode()
        + b'"}'
    )
    assert b"private database password" not in failed.body


@pytest.mark.asyncio
async def test_lifespan_closes_storage_even_when_close_reports_failure(caplog):
    closed = []

    class Storage:
        def close(self):
            closed.append("closed")
            raise RuntimeError("driver shutdown failed")

    fake_app = SimpleNamespace(state=SimpleNamespace(storage=Storage()))
    async with server_app.lifespan(fake_app):
        pass

    assert closed == ["closed"]
    assert "Error closing storage on shutdown" in caplog.text


def test_initialize_storage_retries_neo4j_and_supports_explicit_fallback(tmp_path, monkeypatch):
    config = MemoryConfig()
    config.storage.backend = "neo4j"
    config.storage.data_dir = tmp_path
    config.storage.connect_timeout_seconds = 2
    config.storage.allow_fallback = True
    attempts = []

    class EventuallyAvailable:
        def __init__(self, **kwargs):
            attempts.append(kwargs)
            if len(attempts) == 1:
                raise RuntimeError("temporary outage")

    clock = iter([0.0, 0.5])
    monkeypatch.setattr(server_app, "Neo4jStorage", EventuallyAvailable)
    monkeypatch.setattr(server_app.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(server_app.time, "sleep", lambda _seconds: None)

    storage, backend = server_app.initialize_storage(config)

    assert isinstance(storage, EventuallyAvailable)
    assert backend == "neo4j"
    assert len(attempts) == 2


@pytest.mark.parametrize("allow_fallback", [True, False])
def test_initialize_storage_handles_bounded_neo4j_failure(tmp_path, monkeypatch, allow_fallback):
    config = MemoryConfig()
    config.storage.backend = "neo4j"
    config.storage.data_dir = tmp_path
    config.storage.connect_timeout_seconds = 1
    config.storage.allow_fallback = allow_fallback

    class AlwaysUnavailable:
        def __init__(self, **kwargs):
            raise RuntimeError("neo4j offline")

    clock = iter([0.0, 2.0])
    monkeypatch.setattr(server_app, "Neo4jStorage", AlwaysUnavailable)
    monkeypatch.setattr(server_app.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(server_app.time, "sleep", lambda _seconds: None)

    if allow_fallback:
        storage, backend = server_app.initialize_storage(config)
        assert backend == "sqlite-fallback"
        assert storage.get_stats()["total_memories"] == 0
    else:
        with pytest.raises(RuntimeError, match="fallback is disabled"):
            server_app.initialize_storage(config)


@pytest.mark.asyncio
async def test_scoped_stats_filter_records_intents_and_relationships(client):
    storage = app.state.storage
    repo_id = "stats-team-repo"
    storage.store_repository({"id": repo_id, "name": "Stats", "team_id": "team-a"})
    visible = storage.store_memory(
        "Visible stats memory", repo_id=repo_id, auto_link=False
    )
    hidden = storage.store_memory(
        "Hidden stats memory", repo_id=repo_id, metadata={"team_id": "team-b"}, auto_link=False
    )
    storage.add_relationship(visible, hidden, "supports")
    storage.set_intent("Visible intent", repo_id=repo_id, context={"team_id": "team-a"})
    storage.set_intent("Hidden intent", repo_id=repo_id, context={"team_id": "team-b"})

    stats = server_app._get_scoped_stats(
        repo_id,
        UserContext(user_id="alice", username="alice", team_id="team-a", auth_type="session"),
    )

    assert stats["total_memories"] == 1
    assert stats["active_intents"] == 1
    assert stats["total_relationships"] == 0
    assert stats["memories_by_layer"] == {"episodic": 1}
    assert stats["memories_by_category"] == {"general": 1}
    assert stats["truncated"] is False


@pytest.mark.asyncio
async def test_system_status_reports_unavailable_and_partial_stats(client, monkeypatch):
    admin = UserContext(user_id="admin", username="admin", is_admin=True)

    monkeypatch.setattr(
        server_app,
        "_get_scoped_stats",
        lambda _repo_id, _user: (_ for _ in ()).throw(RuntimeError("stats offline")),
    )
    unavailable = server_app._system_status("repo-a", admin)
    assert unavailable["stats"] == {}
    assert unavailable["stats_status"] == "unavailable"

    monkeypatch.setattr(
        server_app,
        "_get_scoped_stats",
        lambda _repo_id, _user: {"total_memories": 1, "truncated": True},
    )
    partial = server_app._system_status("repo-a", admin)
    assert partial["stats_status"] == "partial"
    assert partial["total_memories"] == 1


class _FakeGraphRecall:
    def __init__(self, storage):
        self.storage = storage

    @staticmethod
    def _result(mode, memory_id):
        return {
            "mode": mode,
            "query": "graph query",
            "nodes": [
                {
                    "id": memory_id,
                    "content": "Graph memory",
                    "layer": "episodic",
                    "category": "note",
                    "importance": 0.5,
                    "repo_id": "repo-a",
                    "relevance_score": 1.0,
                    "relevance_factors": {},
                }
            ],
            "edges": [],
            "omitted": [],
            "limits": {"depth": 1, "token_budget": 1000, "nodes": 1, "edges": 0},
            "explanation": "Graph result",
        }

    def neighbors(self, *, memory_id, **kwargs):
        return self._result("neighbors", memory_id)

    def path(self, *, source_id, **kwargs):
        return self._result("path", source_id)

    def why_relevant(self, *, memory_id, **kwargs):
        return self._result("why_relevant", memory_id)


@pytest.mark.asyncio
async def test_graph_recall_boundary_routes_forward_and_filter_results(client, monkeypatch):
    memory_id = _stored_memory("repo-a", "Graph route memory")
    monkeypatch.setattr(server_app, "GraphRecall", _FakeGraphRecall)
    headers = {"X-API-KEY": "test_key"}

    neighbors = await client.post(
        "/graph-recall/neighbors",
        json={"memory_id": memory_id, "repo_id": "repo-a"},
        headers=headers,
    )
    path = await client.post(
        "/graph-recall/path",
        json={"source_id": memory_id, "target_id": memory_id, "repo_id": "repo-a"},
        headers=headers,
    )
    explanation = await client.post(
        "/graph-recall/why-relevant",
        json={"query": "graph", "memory_id": memory_id, "repo_id": "repo-a"},
        headers=headers,
    )

    assert neighbors.status_code == path.status_code == explanation.status_code == 200
    assert neighbors.json()["mode"] == "neighbors"
    assert path.json()["mode"] == "path"
    assert explanation.json()["mode"] == "why_relevant"
    assert all(
        response.json()["nodes"][0]["id"] == memory_id
        for response in (neighbors, path, explanation)
    )


@pytest.mark.asyncio
async def test_favicon_returns_no_content_when_no_static_icon_exists(client, tmp_path, monkeypatch):
    monkeypatch.setattr(server_app, "STATIC_DIR", tmp_path)

    response = await client.get("/favicon.ico")

    assert response.status_code == 204
    assert response.content == b""


def test_embedding_status_describes_forbidden_connections_and_connected_provider(monkeypatch):
    error = RuntimeError("forbidden")
    error.status_code = 403
    error_code, message = server_app.describe_embedding_connection_error("openrouter", error)
    assert error_code == "HTTP 403"
    assert "denied access" in message

    config = MemoryConfig()
    config.embedding.provider = "openai"
    monkeypatch.setattr(
        "visp_memory.core.embeddings.get_embedding_provider",
        lambda _config, verify=False: SimpleNamespace(provider_name="openai", model="small"),
    )
    provider, runtime = server_app.get_server_embedding_runtime(config)
    assert provider.provider_name == "openai"
    assert runtime["embedding_driver_status"] == "connected"
    status = server_app.get_runtime_status(config, provider)
    assert status["embedding_driver_connected"] is True
    assert status["embedding_effective_provider"] == "openai"
