"""Connect edits the active config, preserving simple YAML text."""

import json

import pytest
import yaml

from tests.cli.test_connect import _healthy_remote, runner
from visp_memory.config import MemoryConfig
from visp_memory.interfaces.cli import app


@pytest.fixture(autouse=True)
def isolated_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.delenv("VISP_MEMORY_CONFIG", raising=False)
    _healthy_remote(monkeypatch)


def _connect(*args):
    result = runner.invoke(app, ["connect", "--server-url", "http://127.0.0.1:9123", *args])
    assert result.exit_code == 0, result.output
    return result


def test_preserves_yaml_comments_and_unrelated_bytes(tmp_path):
    path = tmp_path / "visp-memory.yaml"
    original = (
        "# project notes\nrepo_id: old # scope\ncustom: 'verbatim' # keep\n"
        "storage: # storage notes\n  mode: local # mode notes\n"
        "  server_url: 'http://localhost:8000' # endpoint\n"
        "  data_dir: .private/data # keep path\n"
    )
    path.write_text(original, encoding="utf-8")
    _connect("--repo", "new")
    text = path.read_text(encoding="utf-8")
    for comment in ("# project notes", "# scope", "# storage notes", "# mode notes", "# endpoint"):
        assert comment in text
    assert "custom: 'verbatim' # keep\n" in text
    assert "  data_dir: .private/data # keep path\n" in text
    assert yaml.safe_load(text)["repo_id"] == "new"
    _connect("--repo", "new")
    assert path.read_text(encoding="utf-8") == text
    assert not path.with_name(path.name + ".backup").exists()


@pytest.mark.parametrize("name", ["visp-memory.json", ".visp-memory/config.yaml"])
def test_updates_discovered_config_from_child_without_shadowing(tmp_path, monkeypatch, name):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {"repo_id": "existing", "storage": {"data_dir": ".private/data"}}
    path.write_text(json.dumps(document) if path.suffix == ".json" else yaml.safe_dump(document))
    child = tmp_path / "child"
    child.mkdir()
    monkeypatch.chdir(child)
    _connect()
    loaded = MemoryConfig.find_and_load()
    assert loaded.repo_id == "existing"
    assert loaded.storage.mode == "client"
    assert loaded.storage.data_dir == tmp_path / ".private/data"
    assert not (tmp_path / "visp-memory.yaml").exists()
    assert not (child / "visp-memory.yaml").exists()


@pytest.mark.parametrize("content", [
    "# flow mapping\nrepo_id: old\nstorage: {mode: local, data_dir: .private/data}\n",
    "# alias\nbase: &base\n  data_dir: .private/data\nrepo_id: old\nstorage: *base\n",
])
def test_complex_yaml_saves_backup_and_warns(tmp_path, content):
    path = tmp_path / "visp-memory.yaml"
    path.write_text(content, encoding="utf-8")
    result = _connect()
    assert path.with_name(path.name + ".backup").read_text(encoding="utf-8") == content
    assert "comments were not kept" in result.output.lower()
    assert yaml.safe_load(path.read_text())["storage"]["mode"] == "client"


def test_explicit_config_is_updated(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    path.write_text('{"repo_id": "explicit"}', encoding="utf-8")
    monkeypatch.setenv("VISP_MEMORY_CONFIG", str(path))
    _connect()
    assert json.loads(path.read_text())["storage"]["mode"] == "client"
    assert not (tmp_path / "visp-memory.yaml").exists()


@pytest.mark.parametrize("content", [
    "# comment only", "repo_id: old # scope", "custom: true\n",
    "repo_id: old\nstorage:\n  nested:\n    key: value\nproject_type: code\n",
    "repo_id: old\nstorage:\n  data_dir: 'folder # one' # path\n",
    "repo_id: old\nstorage: null # storage notes\n",
    "repo_id: old\nstorage: # storage notes\n",
])
def test_simple_yaml_insertions_preserve_text(tmp_path, content):
    path = tmp_path / "visp-memory.yaml"
    path.write_text(content, encoding="utf-8")
    _connect()
    text = path.read_text()
    assert yaml.safe_load(text)["storage"]["mode"] == "client"
    assert yaml.safe_load(text)["repo_id"] == (
        "old" if content.startswith("repo_id:") else tmp_path.name
    )
    assert not path.with_name(path.name + ".backup").exists()
    for line in content.splitlines():
        if line.startswith("storage:") and "#" in line:
            assert line[line.index("#"):] in text
        elif not line.startswith("repo_id:"):
            assert line in text


def test_complex_yaml_does_not_overwrite_existing_backup(tmp_path):
    path = tmp_path / "visp-memory.yaml"
    original = "# notes\nrepo_id: old\nstorage: {mode: local}\n"
    path.write_text(original, encoding="utf-8")
    backup = tmp_path / "visp-memory.yaml.backup"
    backup.write_text("earlier backup", encoding="utf-8")
    _connect()
    assert backup.read_text() == "earlier backup"
    assert (tmp_path / "visp-memory.yaml.backup.1").read_text() == original
