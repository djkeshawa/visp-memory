from pathlib import Path

from llm_memory.config import MemoryConfig, ServerConfig
from llm_memory.core.embeddings import NoOpProvider
from llm_memory.server import app as server_app
from llm_memory.server.app import get_cors_options, get_runtime_status, get_server_embedding_fn


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


def test_file_config_env_overrides_include_deployment_settings(monkeypatch):
    config = MemoryConfig()

    monkeypatch.setenv("LLM_MEMORY_STORAGE_DATA_DIR", "/tmp/llm-memory-data")
    monkeypatch.setenv("LLM_MEMORY_SERVER_AUTH_ENABLED", "false")
    monkeypatch.setenv("LLM_MEMORY_SERVER_ALLOW_ANONYMOUS", "true")
    monkeypatch.setenv("LLM_MEMORY_SERVER_API_KEYS", "key-one,key-two")
    monkeypatch.setenv("LLM_MEMORY_SERVER_JWT_EXPIRY_HOURS", "12")

    config.apply_env_overrides()

    assert config.storage.data_dir == Path("/tmp/llm-memory-data")
    assert config.server.auth_enabled is False
    assert config.server.allow_anonymous is True
    assert config.server.api_keys == ["key-one", "key-two"]
    assert config.server.jwt_expiry_hours == 12


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
    assert "neo4j_password" not in status
