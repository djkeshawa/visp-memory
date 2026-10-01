"""Owner proof is scoped to the literal bind, including legacy record upgrades."""

import json

import pytest

from visp_memory.core.remote.owner_auth import owner_token_path_for_request
from visp_memory.server.owner_token import cleanup_owner_token_files, create_owner_token_files


@pytest.mark.parametrize("bind", ["127.0.0.1", "::1", "localhost"])
def test_proof_only_for_recorded_literal_bind(tmp_path, monkeypatch, bind):
    monkeypatch.setattr("visp_memory.core.remote.owner_auth.run_dir", lambda: tmp_path)
    host = f"[{bind}]" if ":" in bind else bind
    url = f"http://{host}:8765"
    files = create_owner_token_files(port=8765, url=url, data_dir=tmp_path, directory=tmp_path)
    try:
        metadata = json.loads(files.server_path.read_text())
        assert metadata["bind_host"] == bind
        assert owner_token_path_for_request(url, url + "/maintenance") == files.token_path
        for other in {"localhost", "127.0.0.1", "[::1]"} - {host}:
            other_url = f"http://{other}:8765"
            assert owner_token_path_for_request(other_url, other_url) is None
        assert owner_token_path_for_request(url, "http://127.0.0.1:9999") is None
    finally:
        cleanup_owner_token_files(files)


@pytest.mark.parametrize("recorded", ["127.0.0.1", "[::1]", "localhost"])
def test_legacy_proof_needs_matching_record(tmp_path, monkeypatch, recorded):
    monkeypatch.setattr("visp_memory.core.remote.owner_auth.run_dir", lambda: tmp_path)
    token = tmp_path / "owner-8765.token"
    token.write_text("legacy")
    url = f"http://{recorded}:8765"
    assert owner_token_path_for_request(url, url) is None
    (tmp_path / "server-8765.json").write_text(json.dumps({"url": url}))
    assert owner_token_path_for_request(url, url) == token
    other = "http://localhost:8765" if recorded != "localhost" else "http://127.0.0.1:8765"
    assert owner_token_path_for_request(other, other) is None


def test_two_literal_binds_have_separate_proof_files(tmp_path):
    first = create_owner_token_files(port=8765, url="http://127.0.0.1:8765",
                                     data_dir=tmp_path, directory=tmp_path)
    second = create_owner_token_files(port=8765, url="http://[::1]:8765",
                                      data_dir=tmp_path, directory=tmp_path)
    try:
        assert first.token_path != second.token_path
        assert first.server_path != second.server_path
        cleanup_owner_token_files(second)
        assert first.token_path.read_text() == first.token
        assert first.server_path.exists()
    finally:
        cleanup_owner_token_files(first)
        cleanup_owner_token_files(second)


def test_rotated_legacy_proof_is_not_sent_after_bind_changes(tmp_path, monkeypatch):
    from unittest.mock import Mock

    from visp_memory.core.remote.owner_auth import resend_with_rotated_token

    monkeypatch.setattr("visp_memory.core.remote.owner_auth.run_dir", lambda: tmp_path)
    path = tmp_path / "owner-8765.token"
    path.write_text("new-token")
    (tmp_path / "server-8765.json").write_text(json.dumps({"url": "http://127.0.0.1:8765"}))
    send = Mock()
    resend_with_rotated_token(send, "GET", "http://localhost:8765/maintenance", {}, path, "old")
    assert "X-Visp-Owner-Token" not in send.call_args.kwargs["headers"]


@pytest.mark.parametrize("bind", ["127.0.0.1", "[::1]", "localhost"])
def test_client_never_sends_proof_to_another_loopback_literal(tmp_path, monkeypatch, bind):
    from tests.core.test_remote_storage import FakeResponse, _ProbeSession
    from visp_memory.core.owner_token import OWNER_TOKEN_HEADER
    from visp_memory.core.remote_storage import RemoteStorage
    from visp_memory.interfaces.connect_discovery import server_records

    monkeypatch.setattr("visp_memory.core.remote.owner_auth.run_dir", lambda: tmp_path)
    files = create_owner_token_files(port=8765, url=f"http://{bind}:8765",
                                     data_dir=tmp_path, directory=tmp_path)
    try:
        assert server_records(tmp_path)[0].bind_host == bind.strip("[]")
        for host in ("127.0.0.1", "[::1]", "localhost"):
            session = _ProbeSession(FakeResponse(200, {}))
            monkeypatch.setattr("visp_memory.core.remote_storage.requests.Session", lambda: session)
            storage = RemoteStorage(f"http://{host}:8765")
            headers = session.calls[0][2].get("headers", {})
            assert headers.get(OWNER_TOKEN_HEADER) == (files.token if host == bind else None)
            storage.close()
    finally:
        cleanup_owner_token_files(files)
