"""Client-mode doctor inspects the server's configured repository scope."""
import json
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

from tests.core.test_remote_storage import FakeResponse, remote_storage_with
from visp_memory.config import MemoryConfig
from visp_memory.core.intent_usage import check_intent_usage
from visp_memory.interfaces import cli


@pytest.fixture
def remote_diagnostics(monkeypatch, tmp_path):
    config = MemoryConfig(repo_id="org/project")
    config.storage.mode = "client"
    config.storage.data_dir = tmp_path / "must-not-create"
    config.storage.server_url = "http://memory.example"
    config.storage.api_key = "test-pat"
    config.embedding.provider = "noop"
    storage = remote_storage_with(FakeResponse())
    usage = {"exists": True, "memories": 2, "active_intents": 0, "total_intents": 0}
    registration = {
        "exists": True, "project_scopes": [config.repo_id], "unregistered_scopes": [],
    }

    def get(url, params=None):
        storage.session.get_calls.append((url, params))
        return FakeResponse(200, usage if url.endswith("/intents/usage") else registration)

    storage.session.get = get
    session = storage.session

    def create(**kwargs):
        # Each diagnostic owns a new client; share only the fake transport log.
        client = remote_storage_with(FakeResponse())
        client.session = session
        client.repo_id = kwargs["repo_id"]
        return client

    constructor = Mock(side_effect=create)
    monkeypatch.setattr("visp_memory.core.remote_storage.RemoteStorage", constructor)
    monkeypatch.setattr(cli, "load_config", lambda: config)
    return config, storage, constructor


def test_doctor_client_mode_uses_scoped_remote_inspections(cli_env, remote_diagnostics):
    config, storage, constructor = remote_diagnostics
    result = CliRunner().invoke(cli.app, ["doctor", "--format", "json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["repositories"]["status"] == "registered"
    assert payload["repositories"]["project_scopes"] == ["org/project"]
    assert payload["intent_usage"]["status"] == "never_used"
    assert payload["intent_usage"]["memories"] == 2
    assert storage.session.get_calls == [
        ("http://memory.example/repos/org%2Fproject/registration", None),
        ("http://memory.example/intents/usage", {"repo_id": "org/project"}),
    ]
    assert storage.session.closed
    assert not config.storage.data_dir.exists()
    assert constructor.call_args.kwargs["repo_id"] == config.repo_id
    assert constructor.call_args.kwargs["api_key"] == "test-pat"


@pytest.mark.parametrize("status", [404, 403, 500])
def test_remote_doctor_errors_are_unreadable_not_clean(remote_diagnostics, status):
    config, storage, _ = remote_diagnostics
    storage.session.get = lambda *args, **kwargs: FakeResponse(status, {"detail": "Not Found"})
    assert cli._repository_registration_status(config)["status"] == "unreadable"
    assert check_intent_usage(config).status == "unreadable"
    assert storage.session.closed


def test_remote_doctor_requires_scope_before_connecting(remote_diagnostics):
    config, _, constructor = remote_diagnostics
    config.repo_id = None
    report = cli._repository_registration_status(config)
    assert report["status"] == "unreadable"
    assert "repo_id is required" in report["status_message"]
    assert check_intent_usage(config).status == "unreadable"
    constructor.assert_not_called()


@pytest.mark.parametrize("payload", [
    {},
    {"exists": "yes", "project_scopes": [], "unregistered_scopes": []},
    {"exists": True, "project_scopes": [123], "unregistered_scopes": []},
    {"exists": True, "memories": "2", "active_intents": 0, "total_intents": 0},
])
def test_remote_doctor_invalid_reports_are_unreadable(remote_diagnostics, payload):
    config, storage, _ = remote_diagnostics
    storage.session.get = lambda *args, **kwargs: FakeResponse(200, payload)
    assert cli._repository_registration_status(config)["status"] == "unreadable"
    assert check_intent_usage(config).status == "unreadable"
    assert storage.session.closed
