"""Regression tests for saving and loading visp-memory.yaml."""

import pytest
import yaml

from visp_memory.config import ENV_OVERRIDES, SECRET_CONFIG_FIELDS, MemoryConfig


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ENV_OVERRIDES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("VISP_MEMORY_CAPTURE_LLM_PROVIDER", raising=False)


def test_save_never_writes_environment_secrets_into_the_config_file(tmp_path, monkeypatch):
    secrets = {
        "EMBEDDING_API_KEY": "sk-embed-secret",
        "VISP_MEMORY_LLM_API_KEY": "sk-llm-secret",
        "VISP_MEMORY_API_KEY": "client-api-secret",
        "VISP_MEMORY_JWT_TOKEN": "jwt-token-secret",
        "NEO4J_PASSWORD": "neo4j-secret",
        "VISP_MEMORY_JWT_SECRET": "jwt-signing-secret",
        "VISP_MEMORY_SERVER_API_KEYS": "server-key-1,server-key-2",
        "VISP_MEMORY_BOOTSTRAP_ADMIN_PASSWORD": "admin-secret",
    }
    for name, value in secrets.items():
        monkeypatch.setenv(name, value)
    config = MemoryConfig(project_name="demo")
    config.apply_env_overrides()
    assert config.server.api_keys == ["server-key-1", "server-key-2"]

    path = tmp_path / "visp-memory.yaml"
    config.save(path)
    written = path.read_text(encoding="utf-8")

    for value in secrets.values():
        for part in value.split(","):
            assert part not in written
    data = yaml.safe_load(written)
    for section, field_name in SECRET_CONFIG_FIELDS:
        assert field_name not in data[section]
    assert data["project_name"] == "demo"

    # Loading the saved file in the same environment still yields the secrets.
    reloaded = MemoryConfig.from_file(path)
    assert reloaded.embedding.api_key == "sk-embed-secret"
    assert reloaded.server.jwt_secret == "jwt-signing-secret"


def test_capture_llm_provider_env_still_applies_after_init_saved_the_file(
    tmp_path, monkeypatch
):
    path = tmp_path / "visp-memory.yaml"
    MemoryConfig(project_name="demo").save(path)  # what `visp-memory init` does
    assert "llm_provider" in yaml.safe_load(path.read_text())["capture"]

    monkeypatch.setenv("VISP_MEMORY_CAPTURE_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("VISP_MEMORY_CAPTURE_LLM_MODEL", "claude-test")
    config = MemoryConfig.from_file(path)

    assert config.capture.llm_provider == "anthropic"
    assert config.capture.llm_model == "claude-test"


@pytest.mark.parametrize("content", ["", "\n", "# only a comment\n# and another\n"])
def test_empty_or_comment_only_yaml_loads_defaults(tmp_path, content):
    path = tmp_path / "visp-memory.yaml"
    path.write_text(content, encoding="utf-8")

    config = MemoryConfig.from_file(path)

    assert config.project_name == "default"


def test_empty_json_config_loads_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("  \n", encoding="utf-8")
    assert MemoryConfig.from_file(path).project_name == "default"


@pytest.mark.parametrize("content", ["- a\n- b\n", "just a string\n", "42\n"])
def test_non_mapping_yaml_is_a_clear_error(tmp_path, content):
    path = tmp_path / "visp-memory.yaml"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError, match="must contain a mapping of settings"):
        MemoryConfig.from_file(path)
