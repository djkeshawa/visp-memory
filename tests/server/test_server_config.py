from pathlib import Path

import pytest

from visp_memory.config import LLMConfig, MemoryConfig, ServerConfig
from visp_memory.core.embeddings import NoOpProvider
from visp_memory.server import app as server_app
from visp_memory.server.app import (
    describe_embedding_connection_error,
    get_cors_options,
    get_runtime_status,
    get_server_embedding_fn,
    get_server_embedding_runtime,
)
from visp_memory.server.schemas import IntentEvaluationRequest


def test_default_cors_options_are_not_wildcard():
    config = MemoryConfig()

    options = get_cors_options(config)

    assert "*" not in options["allow_origins"]
    assert "http://localhost:3000" in options["allow_origins"]
    assert options["allow_credentials"] is True


def test_wildcard_cors_disables_credentials():
    config = MemoryConfig()
    config.server.cors_origins = ["*"]
    config.server.cors_allow_credentials = True

    options = get_cors_options(config)

    assert options["allow_origins"] == ["*"]
    assert options["allow_credentials"] is False


def test_cors_origins_accept_comma_separated_values():
    config = ServerConfig(cors_origins="https://app.example,http://localhost:3000")

    assert config.cors_origins == [
        "https://app.example",
        "http://localhost:3000",
    ]


def test_cors_origins_accept_json_list_values():
    config = ServerConfig(cors_origins='["https://app.example", "http://localhost:3000"]')

    assert config.cors_origins == [
        "https://app.example",
        "http://localhost:3000",
    ]


def test_file_config_env_overrides_include_deployment_settings(monkeypatch):
    config = MemoryConfig()

    monkeypatch.setenv("VISP_MEMORY_STORAGE_DATA_DIR", "/tmp/visp-memory-data")
    monkeypatch.setenv("VISP_MEMORY_SERVER_AUTH_ENABLED", "false")
    monkeypatch.setenv("VISP_MEMORY_SERVER_ALLOW_ANONYMOUS", "true")
    monkeypatch.setenv("VISP_MEMORY_SERVER_API_KEYS", "key-one,key-two")
    monkeypatch.setenv("VISP_MEMORY_SERVER_JWT_EXPIRY_HOURS", "12")

    config.apply_env_overrides()

    assert config.storage.data_dir == Path("/tmp/visp-memory-data")
    assert config.server.auth_enabled is False
    assert config.server.allow_anonymous is True
    assert config.server.api_keys == ["key-one", "key-two"]
    assert config.server.jwt_expiry_hours == 12


def test_legacy_auto_complete_config_and_env_are_accepted_but_deprecated(monkeypatch):
    config = MemoryConfig(llm={"intent_auto_complete": True})
    monkeypatch.setenv("VISP_MEMORY_LLM_INTENT_AUTO_COMPLETE", "false")

    config.apply_env_overrides()

    with pytest.warns(DeprecationWarning, match="ineffective"):
        assert config.llm.intent_auto_complete is False
    assert LLMConfig.model_json_schema()["properties"]["intent_auto_complete"]["deprecated"]
    request_schema = IntentEvaluationRequest.model_json_schema()
    assert request_schema["properties"]["allow_auto_complete"]["deprecated"]


def test_dashboard_file_path_resolves_exported_routes(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("dashboard")
    (tmp_path / "graph.html").write_text("graph")
    (tmp_path / "recall").mkdir()
    (tmp_path / "recall" / "index.html").write_text("recall")
    (tmp_path / "_next").mkdir()
    (tmp_path / "_next" / "asset.js").write_text("asset")

    monkeypatch.setattr(server_app, "STATIC_DIR", tmp_path)

    assert server_app.dashboard_file_path("").name == "index.html"
    assert server_app.dashboard_file_path("graph") == tmp_path / "graph.html"
    assert server_app.dashboard_file_path("recall") == tmp_path / "recall" / "index.html"
    assert server_app.dashboard_file_path("_next/asset.js") == tmp_path / "_next" / "asset.js"
    assert server_app.dashboard_file_path("missing") == tmp_path / "index.html"


def test_dashboard_file_path_rejects_traversal_outside_static_dir(tmp_path, monkeypatch):
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("dashboard")
    outside_file = tmp_path / "secret.txt"
    outside_file.write_text("secret")

    monkeypatch.setattr(server_app, "STATIC_DIR", static_dir)

    assert server_app.dashboard_file_path("../secret.txt") == static_dir / "index.html"


def test_server_embedding_fn_uses_configured_noop_provider():
    config = MemoryConfig()
    config.embedding.provider = "noop"

    embedding = get_server_embedding_fn(config)

    assert embedding("demo")[:3] == [1.0, 0.0, 0.0]


def test_runtime_status_exposes_non_secret_deployment_fields():
    config = MemoryConfig()
    config.repo_id = "repo-a"
    config.storage.backend = "neo4j"
    config.storage.mode = "server"
    config.embedding.provider = "noop"

    status = get_runtime_status(config, NoOpProvider())

    assert status["repo_id"] == "repo-a"
    assert status["storage_backend"] == "neo4j"
    assert status["storage_mode"] == "server"
    assert status["embedding_provider"] == "noop"
    assert status["embedding_effective_provider"] == "noop"
    assert status["embedding_driver_status"] == "disabled"
    assert status["embedding_driver_connected"] is False
    assert "neo4j_password" not in status


def test_initialize_storage_uses_arcadedb_backend(tmp_path, monkeypatch):
    created = {}

    class FakeArcadeDbStorage:
        def __init__(self, data_dir, embedding_fn=None, embedding_dimension=None):
            created["data_dir"] = data_dir
            created["embedding_fn"] = embedding_fn
            created["embedding_dimension"] = embedding_dimension

    class FakeEmbeddingProvider:
        dimension = 7

    monkeypatch.setattr(server_app, "ArcadeDbStorage", FakeArcadeDbStorage)
    config = MemoryConfig()
    config.storage.backend = "arcadedb"
    config.storage.data_dir = tmp_path

    storage, backend = server_app.initialize_storage(
        config,
        embedding_fn=lambda text: [float(len(text))],
        embedding_provider=FakeEmbeddingProvider(),
    )

    assert isinstance(storage, FakeArcadeDbStorage)
    assert backend == "arcadedb"
    assert created["data_dir"] == tmp_path
    assert created["embedding_dimension"] == 7


def test_initialize_storage_does_not_fallback_for_arcadedb(tmp_path, monkeypatch):
    class FailingArcadeDbStorage:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("arcadedb unavailable")

    monkeypatch.setattr(server_app, "ArcadeDbStorage", FailingArcadeDbStorage)
    config = MemoryConfig()
    config.storage.backend = "arcadedb"
    config.storage.data_dir = tmp_path

    with pytest.raises(RuntimeError, match="arcadedb unavailable"):
        server_app.initialize_storage(config)


def test_server_embedding_runtime_marks_default_server_fallback_inactive(monkeypatch):
    monkeypatch.delenv("VISP_MEMORY_EMBEDDING_PROVIDER", raising=False)
    config = MemoryConfig()
    config.embedding.provider = "sentence-transformers"

    provider, status = get_server_embedding_runtime(config)

    assert provider is None
    assert status["embedding_driver_status"] == "not_configured"
    assert status["embedding_driver_connected"] is False


def test_server_embedding_runtime_marks_failed_explicit_driver(monkeypatch):
    config = MemoryConfig()
    config.embedding.provider = "openrouter"

    def fail_provider(_config, *, verify=False):
        assert verify is True
        raise RuntimeError("bad key")

    import visp_memory.core.embeddings as embeddings

    monkeypatch.setattr(embeddings, "get_embedding_provider", fail_provider)

    provider, status = get_server_embedding_runtime(config)

    assert provider.provider_name == "noop"
    assert status["embedding_driver_status"] == "failed"
    assert status["embedding_driver_connected"] is False
    assert status["embedding_connection_error"] == "RuntimeError"


def test_embedding_connection_error_describes_openrouter_401():
    error = RuntimeError("Unauthorized")
    error.status_code = 401

    error_name, message = describe_embedding_connection_error("openrouter", error)

    assert error_name == "HTTP 401"
    assert "rejected the configured credentials" in message
