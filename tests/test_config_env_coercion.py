import os
from types import SimpleNamespace
from typing import get_args

import pytest

from visp_memory.config import ENV_OVERRIDES, MemoryConfig
from visp_memory.server.auth import is_local_owner_request


def _unwrap_optional(annotation):
    args = get_args(annotation)
    if args and type(None) in args:
        return next(arg for arg in args if arg is not type(None))
    return annotation


def _field_annotation(path: tuple[str, ...]):
    model_type = MemoryConfig
    for attr in path[:-1]:
        model_type = _unwrap_optional(model_type.model_fields[attr].annotation)
    return _unwrap_optional(model_type.model_fields[path[-1]].annotation)


BOOL_ENV_NAMES = [
    env_name
    for env_name, path in ENV_OVERRIDES.items()
    if _field_annotation(path) is bool
]


@pytest.mark.parametrize("env_name", BOOL_ENV_NAMES)
@pytest.mark.parametrize(("raw", "expected"), [("false", False), ("true", True)])
def test_every_mapped_bool_env_is_coerced_from_its_field_type(
    monkeypatch, env_name, raw, expected
):
    for mapped_name in ENV_OVERRIDES:
        monkeypatch.delenv(mapped_name, raising=False)
    config = MemoryConfig()

    monkeypatch.setenv(env_name, raw)
    config.apply_env_overrides()

    target = config
    for attr in ENV_OVERRIDES[env_name]:
        target = getattr(target, attr)
    assert target is expected


def test_bool_env_coverage_includes_local_owner_and_shared_mode():
    assert "VISP_MEMORY_SERVER_LOCAL_OWNER_MODE" in BOOL_ENV_NAMES
    assert "VISP_MEMORY_SERVER_SHARED" in BOOL_ENV_NAMES


def test_false_local_owner_env_does_not_grant_local_owner(monkeypatch):
    for mapped_name in ENV_OVERRIDES:
        monkeypatch.delenv(mapped_name, raising=False)
    config = MemoryConfig()
    monkeypatch.setenv("VISP_MEMORY_SERVER_LOCAL_OWNER_MODE", "false")

    config.apply_env_overrides()

    request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))
    assert config.server.local_owner_mode is False
    assert is_local_owner_request(request, config) is False


def test_explicit_config_is_loaded_first_and_resolves_relative_data_dir(
    tmp_path, monkeypatch
):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "visp-memory.yaml").write_text(
        "repo_id: discovered\nstorage:\n  data_dir: wrong-data\n",
        encoding="utf-8",
    )
    config_dir = tmp_path / "shared"
    config_dir.mkdir()
    config_path = config_dir / "config.yaml"
    config_path.write_text(
        "repo_id: selected\nstorage:\n  data_dir: relative-data\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(project_dir)
    monkeypatch.setenv(
        "VISP_MEMORY_CONFIG",
        os.path.relpath(config_path, project_dir),
    )

    config = MemoryConfig.find_and_load()

    assert config.repo_id == "selected"
    assert config.storage.data_dir == (config_dir / "relative-data").resolve()


def test_explicit_config_missing_file_has_a_clear_error(tmp_path, monkeypatch):
    missing = tmp_path / "missing.yaml"
    monkeypatch.setenv("VISP_MEMORY_CONFIG", str(missing))

    with pytest.raises(FileNotFoundError, match="VISP_MEMORY_CONFIG.*missing.yaml"):
        MemoryConfig.find_and_load()
