"""A relative storage.data_dir keeps the 0.7.10 meaning for every discovered config."""

import json

import pytest

from visp_memory.config import MemoryConfig

CANDIDATES = ("visp-memory.yaml", "visp-memory.json", ".visp-memory/config.yaml")


@pytest.fixture(autouse=True)
def _no_ambient_config(monkeypatch):
    monkeypatch.delenv("VISP_MEMORY_CONFIG", raising=False)
    monkeypatch.delenv("VISP_MEMORY_STORAGE_DATA_DIR", raising=False)


def _write_config(project, name, data_dir):
    path = project / name
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {"repo_id": "demo"}
    if data_dir is not None:
        body["storage"] = {"data_dir": str(data_dir)}
    # JSON is valid YAML, so one serialisation serves every candidate name.
    path.write_text(json.dumps(body), encoding="utf-8")


@pytest.mark.parametrize("name", CANDIDATES)
@pytest.mark.parametrize("kind", ["default", "relative", "absolute"])
def test_walked_config_resolves_data_dir_against_the_walked_directory(tmp_path, name, kind):
    project = (tmp_path / "project").resolve()
    start = project / "src" / "package"
    start.mkdir(parents=True)
    absolute = (tmp_path / "elsewhere" / "store").resolve()
    configured = {"default": None, "relative": "custom/store", "absolute": absolute}[kind]
    _write_config(project, name, configured)

    config = MemoryConfig.find_and_load(start)

    # What 0.7.10 produced: the directory holding the candidate, not the file's parent.
    expected = {
        "default": project / ".visp-memory" / "data",
        "relative": project / "custom" / "store",
        "absolute": absolute,
    }[kind]
    assert config.repo_id == "demo"
    assert config.storage.data_dir == expected


def test_explicit_config_resolves_data_dir_against_the_file_directory(tmp_path, monkeypatch):
    """The shared server's ~/.visp-memory/config.yaml relies on this different rule."""
    root = (tmp_path / "shared-root").resolve()
    root.mkdir()
    config_path = root / "config.yaml"
    config_path.write_text("storage:\n  data_dir: data\n", encoding="utf-8")
    monkeypatch.setenv("VISP_MEMORY_CONFIG", str(config_path))
    monkeypatch.chdir(tmp_path)

    assert MemoryConfig.find_and_load().storage.data_dir == root / "data"
