import builtins
from pathlib import Path

import pytest

from llm_memory.config import MemoryConfig
from llm_memory.core.arcadedb_storage import (
    ARCADEDB_INSTALL_MESSAGE,
    ArcadeDbDependencyError,
    ArcadeDbStorage,
    load_arcadedb_driver,
)


def test_storage_config_accepts_arcadedb_backend():
    config = MemoryConfig()

    assert config.storage.backend == "sqlite"

    config.storage.backend = "arcadedb"

    assert config.storage.backend == "arcadedb"


def test_arcadedb_optional_extra_declared():
    try:
        import tomllib
    except ImportError:
        pytest.skip("tomllib is unavailable on this Python version")

    pyproject = tomllib.loads(Path("pyproject.toml").read_text())
    extras = pyproject["project"]["optional-dependencies"]

    assert extras["arcadedb"] == ["arcadedb-embedded>=26.4.2,<27"]
    assert "arcadedb" in extras["all"][0]


def test_load_arcadedb_driver_missing_dependency_message(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "arcadedb_embedded":
            raise ImportError("missing arcadedb")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(ArcadeDbDependencyError) as excinfo:
        load_arcadedb_driver()

    assert ARCADEDB_INSTALL_MESSAGE in str(excinfo.value)
    assert "llm-memory[arcadedb]" in str(excinfo.value)


def test_arcadedb_storage_uses_configured_data_dir(monkeypatch, tmp_path):
    driver = object()
    monkeypatch.setattr("llm_memory.core.arcadedb_storage.load_arcadedb_driver", lambda: driver)

    storage = ArcadeDbStorage(tmp_path)

    assert storage.data_dir == tmp_path / "arcadedb"
    assert storage.data_dir.exists()
    assert storage._arcadedb is driver
