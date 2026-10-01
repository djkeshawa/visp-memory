"""Runtime records are local discovery hints, never credential destinations."""

import json
import os

import pytest

from tests.cli.test_connect import _RemoteStorage, runner
from visp_memory.interfaces.cli import app
from visp_memory.interfaces.connect_discovery import discover_shared_server


@pytest.mark.parametrize("url", [
    "http://example.com:8000", "https://127.0.0.1.example.com", "file:///tmp/server",
    "ftp://127.0.0.1:8000", "http://user:password@127.0.0.1:8000", "//localhost:8000",
    "http://127.0.0.1:bad", "http://[::1", "http://localhost:8000/path",
])
def test_unsafe_discovery_records_are_not_probed(tmp_path, url):
    (tmp_path / "server-8000.json").write_text(json.dumps({"pid": os.getpid(), "url": url}))
    probed = []
    found = discover_shared_server(tmp_path, probe=lambda url: probed.append(url) or True)
    assert found is None
    assert probed == []


@pytest.mark.parametrize("url", ["http://localhost:8000", "https://127.0.0.2:8000", "http://[::1]:8000"])
def test_loopback_http_discovery_is_accepted(tmp_path, url):
    (tmp_path / "server-8000.json").write_text(json.dumps({"pid": os.getpid(), "url": url}))
    assert discover_shared_server(tmp_path, probe=lambda _url: True).url == url


@pytest.mark.parametrize("configured", [None, "http://127.0.0.1:8000", "http://127.0.0.1:9123/"])
@pytest.mark.parametrize("credentials", ["environment", "config"])
def test_discovered_url_only_gets_credentials_when_configured(
    tmp_path, monkeypatch, configured, credentials,
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.delenv("VISP_MEMORY_CONFIG", raising=False)
    runtime = tmp_path / "run"
    runtime.mkdir()
    (runtime / "server-9123.json").write_text(json.dumps({
        "pid": os.getpid(), "url": "http://127.0.0.1:9123",
    }))
    monkeypatch.setattr("visp_memory.interfaces.connect.run_dir", lambda: runtime)
    monkeypatch.setattr("visp_memory.interfaces.connect._probe_server", lambda _url: True)
    storage = {}
    if configured:
        storage["server_url"] = configured
    for env, key in [("VISP_MEMORY_API_KEY", "api_key"), ("VISP_MEMORY_JWT_TOKEN", "jwt_token")]:
        monkeypatch.delenv(env, raising=False)
        if credentials == "environment":
            monkeypatch.setenv(env, "test-secret")
        else:
            storage[key] = "test-secret"
    (tmp_path / "visp-memory.json").write_text(json.dumps({"repo_id": "test", "storage": storage}))
    captured = []

    class CapturingRemote(_RemoteStorage):
        def __init__(self, **kwargs):
            super().__init__()
            captured.append(kwargs)

    monkeypatch.setattr("visp_memory.interfaces.connect.RemoteStorage", CapturingRemote)
    result = runner.invoke(app, ["connect"])
    assert result.exit_code == 0, result.output
    expected = "test-secret" if configured and configured.rstrip("/").endswith(":9123") else None
    assert captured[0]["api_key"] == expected
    assert captured[0]["jwt_token"] == expected
