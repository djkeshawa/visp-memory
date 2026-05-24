from types import SimpleNamespace

import pytest

from llm_memory.config import MemoryConfig
from llm_memory.core.indexing import ReindexResult
from llm_memory.server.app import app


@pytest.mark.asyncio
async def test_provider_diagnostics_requires_auth(client):
    response = await client.get("/diagnostics/providers")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_provider_diagnostics_reports_active_openrouter_without_secrets(
    client, monkeypatch
):
    config = MemoryConfig()
    config.embedding.provider = "cloud"
    config.embedding.model = "openai/text-embedding-3-small"
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-secret")
    monkeypatch.setattr("llm_memory.server.routers.diagnostics.load_config", lambda: config)

    previous_provider = getattr(app.state, "embedding_provider", None)
    previous_status = getattr(app.state, "embedding_runtime_status", None)
    app.state.embedding_provider = SimpleNamespace(
        provider_name="openrouter",
        model="openai/text-embedding-3-small",
        dimension=1536,
    )
    app.state.embedding_runtime_status = {
        "embedding_driver_status": "connected",
        "embedding_driver_connected": True,
        "embedding_status_message": "Embedding driver connected.",
    }

    try:
        response = await client.get(
            "/diagnostics/providers", headers={"X-API-KEY": "test_key"}
        )
    finally:
        app.state.embedding_provider = previous_provider
        app.state.embedding_runtime_status = previous_status

    assert response.status_code == 200
    data = response.json()
    providers = {item["provider"]: item for item in data["providers"]}
    assert data["active_provider"] == "cloud"
    assert data["effective_provider"] == "openrouter"
    assert providers["openrouter"]["configured"] is True
    assert providers["openrouter"]["connected"] is True
    assert providers["openrouter"]["status"] == "connected"
    assert "sk-or-secret" not in str(data)


@pytest.mark.asyncio
async def test_provider_diagnostics_get_does_not_verify_providers(client, monkeypatch):
    config = MemoryConfig()
    config.embedding.provider = "openrouter"
    monkeypatch.setattr("llm_memory.server.routers.diagnostics.load_config", lambda: config)

    def fail_if_called(*args, **kwargs):
        raise AssertionError("GET diagnostics should not perform live provider checks")

    monkeypatch.setattr("llm_memory.core.embeddings.get_embedding_provider", fail_if_called)

    response = await client.get(
        "/diagnostics/providers", headers={"X-API-KEY": "test_key"}
    )

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_provider_test_reports_missing_openrouter_config(client, monkeypatch):
    config = MemoryConfig()
    config.embedding.provider = "openrouter"
    config.embedding.api_key = None
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)
    monkeypatch.setattr("llm_memory.server.routers.diagnostics.load_config", lambda: config)

    response = await client.post(
        "/diagnostics/providers/openrouter/test", headers={"X-API-KEY": "test_key"}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["provider"] == "openrouter"
    assert data["connected"] is False
    assert data["status"] == "not_configured"
    assert "OPENROUTER_API_KEY" in data["message"]


@pytest.mark.asyncio
async def test_provider_test_reports_openrouter_401_without_secret(client, monkeypatch):
    config = MemoryConfig()
    config.embedding.provider = "openrouter"
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-secret")
    monkeypatch.setattr("llm_memory.server.routers.diagnostics.load_config", lambda: config)

    class UnauthorizedProviderError(RuntimeError):
        status_code = 401

    def fail_provider(_config, *, verify=False):
        assert verify is True
        raise UnauthorizedProviderError("bad key sk-or-secret")

    monkeypatch.setattr("llm_memory.core.embeddings.get_embedding_provider", fail_provider)

    response = await client.post(
        "/diagnostics/providers/openrouter/test", headers={"X-API-KEY": "test_key"}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["provider"] == "openrouter"
    assert data["connected"] is False
    assert data["status"] == "failed"
    assert data["error_code"] == "HTTP 401"
    assert "rejected the configured credentials" in data["message"]
    assert "sk-or-secret" not in str(data)


@pytest.mark.asyncio
async def test_provider_test_reports_success(client, monkeypatch):
    config = MemoryConfig()
    config.embedding.provider = "openrouter"
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setattr("llm_memory.server.routers.diagnostics.load_config", lambda: config)

    def good_provider(_config, *, verify=False):
        assert verify is True
        return SimpleNamespace(
            provider_name="openrouter",
            model="openai/text-embedding-3-small",
            dimension=1536,
        )

    monkeypatch.setattr("llm_memory.core.embeddings.get_embedding_provider", good_provider)

    response = await client.post(
        "/diagnostics/providers/openrouter/test", headers={"X-API-KEY": "test_key"}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["connected"] is True
    assert data["status"] == "connected"
    assert data["dimension"] == 1536


@pytest.mark.asyncio
async def test_provider_test_unknown_provider_returns_404(client):
    response = await client.post(
        "/diagnostics/providers/not-real/test", headers={"X-API-KEY": "test_key"}
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_embedding_index_status_uses_runtime_provider(client, monkeypatch):
    config = MemoryConfig()
    config.embedding.provider = "cloud"
    monkeypatch.setattr("llm_memory.server.routers.diagnostics.load_config", lambda: config)

    previous_provider = getattr(app.state, "embedding_provider", None)
    previous_storage = app.state.storage
    app.state.embedding_provider = SimpleNamespace(
        provider_name="openrouter",
        model="openai/text-embedding-3-small",
        dimension=1536,
    )
    app.state.storage._embedding_fn = lambda _text: [0.1, 0.2, 0.3]
    app.state.storage._embedding_dimension = 1536
    app.state.storage._uses_noop_embeddings = False
    monkeypatch.setattr(app.state.storage, "_list_vector_collection_names", lambda: [])
    monkeypatch.setattr(app.state.storage, "_count_vector_collection", lambda _name: 0)

    try:
        response = await client.get(
            "/diagnostics/embedding-index", headers={"X-API-KEY": "test_key"}
        )
    finally:
        app.state.embedding_provider = previous_provider
        app.state.storage = previous_storage

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "available"
    assert data["effective_provider"] == "openrouter"
    assert data["dimension"] == 1536
    assert data["matched_memories"] == 0


@pytest.mark.asyncio
async def test_embedding_index_reindex_dry_run_returns_scope(client, monkeypatch):
    config = MemoryConfig()
    config.embedding.provider = "cloud"
    monkeypatch.setattr("llm_memory.server.routers.diagnostics.load_config", lambda: config)

    class FakeStorage:
        def rebuild_embedding_index(self, *, scope, dry_run):
            assert scope.repo_id == "repo-a"
            assert scope.layer == "episodic"
            assert dry_run is True
            return ReindexResult(
                dry_run=True,
                status="ready",
                message="Dry run complete; no embeddings were changed.",
                scope=scope.as_filter_dict(),
                matched_memories=2,
                reindexed_memories=0,
                failed_memories=0,
                dimension=1536,
                active_collections=["memories_episodic_1536"],
                legacy_collections=["memories_episodic_384"],
                errors=[],
            )

    previous_storage = app.state.storage
    app.state.storage = FakeStorage()

    try:
        response = await client.post(
            "/diagnostics/embedding-index/reindex",
            headers={"X-API-KEY": "test_key"},
            json={"repo_id": "repo-a", "layer": "episodic", "dry_run": True},
        )
    finally:
        app.state.storage = previous_storage

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"
    assert data["scope"] == {"repo_id": "repo-a", "layer": "episodic"}
    assert data["matched_memories"] == 2
    assert data["legacy_collections"] == ["memories_episodic_384"]
