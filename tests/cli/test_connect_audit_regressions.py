"""Connect never edits the wrong files, the wrong store, or half a project."""

import json
import os
import subprocess

import pytest
import yaml

from tests.cli.test_connect import _healthy_remote, runner
from tests.cli.test_connect_migration import MigrationRemote
from visp_memory import Memory, MemoryConfig
from visp_memory.config_discovery import find_config_file
from visp_memory.interfaces.cli import app
from visp_memory.interfaces.connect_config import load_document, write_client_config
from visp_memory.interfaces.connect_models import ConnectError

SERVER = "http://127.0.0.1:9123"
_SHARED_ENV = (
    "VISP_MEMORY_CONFIG", "VISP_MEMORY_STORAGE_DATA_DIR",
    "VISP_MEMORY_SERVER_SHARED", "VISP_MEMORY_REPO_ID",
)


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    for name in _SHARED_ENV:
        # setenv first so teardown restores the original state even after
        # configure_shared_server() writes os.environ directly.
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)
    return home


def _start_shared_server_config(monkeypatch):
    from visp_memory.interfaces.shared_server import configure_shared_server

    configure_shared_server(None)
    # A different shell: the server's own environment does not reach `connect`.
    for name in _SHARED_ENV:
        monkeypatch.delenv(name, raising=False)


def test_shared_server_config_is_not_a_project_config_under_home(home, monkeypatch):
    _start_shared_server_config(monkeypatch)
    shared_config = home / ".visp-memory" / "config.yaml"
    shared_before = shared_config.read_bytes()
    project = home / "work" / "proj"
    project.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    monkeypatch.chdir(project)
    _healthy_remote(monkeypatch)

    assert find_config_file() is None
    result = runner.invoke(app, ["connect", "--server-url", SERVER])

    assert result.exit_code == 0, result.output
    assert yaml.safe_load((project / "visp-memory.yaml").read_text())["repo_id"] == "proj"
    assert shared_config.read_bytes() == shared_before
    assert sorted(path.name for path in home.iterdir()) == [".visp-memory", "work"]


def test_connect_refuses_the_shared_server_config_when_selected_explicitly(home, monkeypatch):
    _start_shared_server_config(monkeypatch)
    shared_config = home / ".visp-memory" / "config.yaml"
    before = shared_config.read_bytes()
    monkeypatch.setenv("VISP_MEMORY_CONFIG", str(shared_config))
    monkeypatch.chdir(home)
    _healthy_remote(monkeypatch)

    result = runner.invoke(app, ["connect", "--server-url", SERVER])

    assert result.exit_code == 1
    assert "shared server" in result.output
    assert shared_config.read_bytes() == before


@pytest.fixture
def project(tmp_path, home, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("visp_memory.interfaces.connect.RemoteStorage", MigrationRemote)
    MigrationRemote.repositories = {}
    MigrationRemote.imports = []
    MigrationRemote.graph = {}
    (tmp_path / "visp-memory.yaml").write_text(
        "repo_id: demo\nembedding:\n  provider: noop\n", encoding="utf-8",
    )
    return tmp_path


def test_migration_reads_the_store_named_by_the_environment(project, monkeypatch):
    store = project / "elsewhere"
    monkeypatch.setenv("VISP_MEMORY_STORAGE_DATA_DIR", str(store))
    config = MemoryConfig(repo_id="demo")
    config.storage.data_dir = store
    config.embedding.provider = "noop"
    with Memory(config=config) as memory:
        memory._storage.store_memory("lives in the env store", repo_id="demo")

    result = runner.invoke(app, ["connect", "--server-url", SERVER, "--migrate-local"])

    assert result.exit_code == 0, result.output
    assert str(store.resolve()) in result.output
    assert "Imported 0 local records" not in " ".join(result.output.split())
    assert "lives in the env store" in json.dumps(MigrationRemote.imports)


def test_migration_from_a_missing_local_store_changes_nothing(project, monkeypatch):
    monkeypatch.setenv("VISP_MEMORY_STORAGE_DATA_DIR", str(project / "missing"))
    before = (project / "visp-memory.yaml").read_bytes()

    result = runner.invoke(app, ["connect", "--server-url", SERVER, "--migrate-local"])

    assert result.exit_code == 1
    assert "No local store to migrate" in result.output
    assert (project / "visp-memory.yaml").read_bytes() == before
    assert MigrationRemote.imports == []


@pytest.mark.parametrize("name", [".claude/settings.json", ".mcp.json"])
def test_unparseable_agent_file_is_refused_before_the_config_switches(project, name):
    agent_file = project / name
    agent_file.parent.mkdir(parents=True, exist_ok=True)
    agent_file.write_text("{ // jsonc comment\n}\n", encoding="utf-8")
    before = (project / "visp-memory.yaml").read_bytes()

    result = runner.invoke(
        app, ["connect", "--server-url", SERVER, "--agent-config", "claude-code"],
    )

    assert result.exit_code == 1
    assert "Error:" in result.output
    assert "not valid JSON" in " ".join(result.output.split())
    assert (project / "visp-memory.yaml").read_bytes() == before
    assert not (project / "CLAUDE.md").exists()
    assert MigrationRemote.repositories == {}


def test_yaml_edit_that_stops_parsing_falls_back_to_a_full_rewrite(tmp_path):
    path = tmp_path / "visp-memory.yaml"
    # Appending after an explicit document end makes a second document.
    path.write_text("---\nrepo_id: a\n...\n", encoding="utf-8")

    note = write_client_config(path, load_document(path), "repo", SERVER)

    assert note is not None and "backup" in note.lower()
    assert yaml.safe_load(path.read_text(encoding="utf-8")) == {
        "repo_id": "repo", "storage": {"mode": "client", "server_url": SERVER},
    }


def test_interrupted_config_write_keeps_the_original_file(tmp_path, monkeypatch):
    path = tmp_path / "visp-memory.yaml"
    original = b"repo_id: a\n"
    path.write_bytes(original)

    def interrupted(_source, _target):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", interrupted)
    with pytest.raises(ConnectError, match="disk full"):
        write_client_config(path, load_document(path), "repo", SERVER)

    assert path.read_bytes() == original
    assert sorted(item.name for item in tmp_path.iterdir()) == ["visp-memory.yaml"]
