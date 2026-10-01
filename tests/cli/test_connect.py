import json
import os

import pytest
import yaml
from typer.testing import CliRunner

from visp_memory.interfaces.cli import app
from visp_memory.interfaces.connect import discover_shared_server

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.delenv("VISP_MEMORY_CONFIG", raising=False)


class _Response:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class _Session:
    def get(self, url, **_kwargs):
        if url.endswith("/diagnostics/capabilities"):
            return _Response({"repositories": True})
        return _Response({"status": "online"})


class _RemoteStorage:
    repositories = {}

    def __init__(self, *_args, **_kwargs):
        self.session = _Session()

    def get_repository(self, repo_id):
        return self.repositories.get(repo_id)

    def store_repository(self, repository):
        self.repositories[repository["id"]] = dict(repository)
        return repository["id"]

    def close(self):
        return None


def _healthy_remote(monkeypatch):
    _RemoteStorage.repositories = {}
    monkeypatch.setattr("visp_memory.interfaces.connect.RemoteStorage", _RemoteStorage)


def test_connect_merges_yaml_preserving_unrelated_keys_and_is_idempotent(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    _healthy_remote(monkeypatch)
    config_path = tmp_path / "visp-memory.yaml"
    config_path.write_text(
        "repo_id: existing-repo\n"
        "project_type: code\n"
        "custom:\n"
        "  nested: keep-me\n"
        "storage:\n"
        "  data_dir: .private-memory/data\n"
        "  backend: sqlite\n",
        encoding="utf-8",
    )

    first = runner.invoke(app, ["connect", "--server-url", "http://127.0.0.1:9123"])

    assert first.exit_code == 0, first.output
    merged = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert merged["repo_id"] == "existing-repo"
    assert merged["project_type"] == "code"
    assert merged["custom"] == {"nested": "keep-me"}
    assert merged["storage"] == {
        "data_dir": ".private-memory/data",
        "backend": "sqlite",
        "mode": "client",
        "server_url": "http://127.0.0.1:9123",
    }
    once = config_path.read_bytes()

    second = runner.invoke(app, ["connect", "--server-url", "http://127.0.0.1:9123"])

    assert second.exit_code == 0, second.output
    assert config_path.read_bytes() == once


def test_discover_shared_server_uses_newest_live_responsive_record(tmp_path):
    older = tmp_path / "server-8001.json"
    older.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "url": "http://127.0.0.1:8001",
                "data_dir": str(tmp_path / "old-data"),
            }
        ),
        encoding="utf-8",
    )
    newer = tmp_path / "server-8002.json"
    newer.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "url": "http://127.0.0.1:8002",
                "data_dir": str(tmp_path / "new-data"),
            }
        ),
        encoding="utf-8",
    )
    os.utime(older, (1, 1))
    os.utime(newer, (2, 2))

    discovered = discover_shared_server(
        tmp_path,
        probe=lambda url: url.endswith(":8002"),
    )

    assert discovered is not None
    assert discovered.url == "http://127.0.0.1:8002"
    assert discovered.data_dir == (tmp_path / "new-data").resolve()


def test_connect_reports_how_to_start_a_missing_server(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("visp_memory.interfaces.connect.run_dir", lambda: tmp_path / "run")
    monkeypatch.setattr(
        "visp_memory.interfaces.connect.DEFAULT_SERVER_URL",
        "http://127.0.0.1:9",
    )

    result = runner.invoke(app, ["connect"])

    assert result.exit_code == 1
    assert "http://127.0.0.1:9" in result.output
    assert "start it with `visp-memory serve --shared`" in result.output
    assert not (tmp_path / "visp-memory.yaml").exists()


def test_connect_refuses_to_migrate_the_served_data_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _healthy_remote(monkeypatch)
    data_dir = tmp_path / "shared-data"
    data_dir.mkdir()
    (tmp_path / "visp-memory.yaml").write_text(
        f"repo_id: same-store\nstorage:\n  data_dir: {data_dir}\n",
        encoding="utf-8",
    )
    runtime = tmp_path / "run"
    runtime.mkdir()
    (runtime / "server-9123.json").write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "url": "http://127.0.0.1:9123",
                "data_dir": str(data_dir),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("visp_memory.interfaces.connect.run_dir", lambda: runtime)
    monkeypatch.setattr("visp_memory.interfaces.connect._probe_server", lambda _url: True)
    before = (tmp_path / "visp-memory.yaml").read_bytes()

    result = runner.invoke(app, ["connect", "--migrate-local"])

    assert result.exit_code == 1
    assert "served data directory" in result.output
    assert (tmp_path / "visp-memory.yaml").read_bytes() == before


def test_connect_agent_config_is_repeatable(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _healthy_remote(monkeypatch)
    installed = []
    monkeypatch.setattr(
        "visp_memory.interfaces.connect.install_agent_config",
        lambda target, **_kwargs: installed.append(target) or [],
    )

    result = runner.invoke(
        app,
        [
            "connect",
            "--server-url",
            "http://127.0.0.1:9123",
            "--agent-config",
            "codex",
            "--agent-config",
            "claude-code",
        ],
    )

    assert result.exit_code == 0, result.output
    assert installed == ["codex", "claude-code"]
