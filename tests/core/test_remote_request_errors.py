"""Remote failures carry the facts a caller needs, and a 403 is retried only for a new token."""

import json

import requests

from tests.core.test_remote_storage import FakeResponse, _ProbeSession, remote_storage_with
from visp_memory.core.owner_token import OWNER_TOKEN_HEADER
from visp_memory.core.remote_storage import RemoteStorage


def _owner_storage(monkeypatch, tmp_path, token):
    import visp_memory.core.remote_storage as remote_module

    token_dir = tmp_path / "run"
    token_dir.mkdir()
    token_path = token_dir / "owner-8765.token"
    token_path.write_text(token, encoding="utf-8")
    (token_dir / "server-8765.json").write_text(json.dumps({"url": "http://localhost:8765"}))
    monkeypatch.setattr("visp_memory.core.remote.owner_auth.run_dir", lambda: token_dir)
    session = _ProbeSession(FakeResponse(200, {}))
    monkeypatch.setattr(remote_module.requests, "Session", lambda: session)
    return RemoteStorage("http://localhost:8765"), session, token_path


def test_forbidden_request_is_not_resent_when_the_token_is_unchanged(monkeypatch, tmp_path):
    storage, session, _ = _owner_storage(monkeypatch, tmp_path, "same-token")
    session.responses = [FakeResponse(403, {"detail": "forbidden"})]

    response = storage.session.post("http://localhost:8765/memories", json={"content": "x"})

    assert response.status_code == 403
    # One probe at construction, then exactly one POST: a non-idempotent write
    # must not be sent twice just because it was legitimately refused.
    assert [call[0] for call in session.calls] == ["GET", "POST"]


def test_forbidden_request_is_retried_without_proof_when_the_token_was_removed(
    monkeypatch, tmp_path
):
    storage, session, token_path = _owner_storage(monkeypatch, tmp_path, "old-token")
    session.responses = [FakeResponse(403, {}), FakeResponse(200, {})]
    session.on_response = lambda response: (
        token_path.unlink() if response.status_code == 403 else None
    )

    response = storage.session.get("http://localhost:8765/maintenance/verify")

    assert response.status_code == 200
    assert len(session.calls) == 3
    assert OWNER_TOKEN_HEADER not in session.calls[-1][2]["headers"]


def test_connection_failure_is_marked_unreachable_with_the_server_url():
    storage = remote_storage_with(FakeResponse())
    error = storage._write_error("list intents", requests.ConnectionError("refused"))

    assert error.unreachable is True
    assert error.server_url == "http://memory.example"
    assert error.status_code is None
    assert str(error) == "Failed to list intents on remote memory server: refused"


def test_http_refusal_keeps_status_and_server_detail():
    storage = remote_storage_with(FakeResponse())
    http_error = requests.HTTPError("HTTP 400")
    http_error.response = FakeResponse(400, {"detail": "repo_id is required"})

    error = storage._write_error("list intents", http_error)

    assert error.unreachable is False
    assert error.status_code == 400
    assert error.detail == "repo_id is required"
    assert error.server_url == "http://memory.example"
    assert str(error) == (
        "Failed to list intents on remote memory server: HTTP 400: repo_id is required"
    )


def test_read_timeout_is_not_reported_as_unreachable():
    storage = remote_storage_with(FakeResponse())

    error = storage._write_error("list intents", requests.ReadTimeout("slow"))

    assert error.unreachable is False
    assert error.status_code is None
