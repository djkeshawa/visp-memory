"""Exercise review scope through a real HTTP client without opening sockets."""

import json
from types import SimpleNamespace

import pytest
import requests
from typer.testing import CliRunner

from visp_memory.core.remote_storage import RemoteStorage
from visp_memory.interfaces.cli import app


@pytest.mark.parametrize("decision,status", [("accept", "active"), ("reject", "rejected")])
@pytest.mark.parametrize("scope,success", [("other", True), ("configured", False),
                                           ("__visp_unscoped__", False)])
def test_review_honors_explicit_client_scope(monkeypatch, decision, status, scope, success):
    row = {"id": "proposal", "repo_id": "other", "status": "quarantined", "content": "idea"}
    updates = []

    def request(method, url, **kwargs):
        response = requests.Response()
        response.status_code = 200
        if method == "GET":
            response.status_code = (
                200 if (kwargs.get("params") or {}).get("repo_id") == "other" else 404
            )
            response._content = json.dumps(row).encode()
        else:
            updates.append(kwargs["json"])
            response._content = b"{}"
        return response

    monkeypatch.setattr(requests.Session, "request", lambda _self, *a, **kw: request(*a, **kw))
    storage = RemoteStorage(server_url="http://127.0.0.1:9123", repo_id="configured")
    monkeypatch.setattr("visp_memory.interfaces.cli._memory", SimpleNamespace(
        _storage=storage, config=SimpleNamespace(repo_id="configured"),
    ))
    try:
        result = CliRunner().invoke(app, ["review", decision, "proposal", "--repo", scope])
        assert (result.exit_code == 0) is success, result.output
        assert updates == ([{"status": status, "metadata": {
            "review": {"decision": "accepted" if decision == "accept" else "rejected"},
        }}] if success else [])
        assert storage.repo_id == "configured"
    finally:
        storage.close()


@pytest.mark.parametrize("command", ["recall", "propose"])
def test_contract_endpoint_help_explains_client_contacts(command):
    result = CliRunner().invoke(
        app, ["contract", command, "--help"], color=False, env={"COLUMNS": "200"},
    )
    assert result.exit_code == 0
    plain = " ".join(result.output.split())
    assert "contacts nothing" not in plain
    assert "client mode" in plain
    assert "configured server" in plain
