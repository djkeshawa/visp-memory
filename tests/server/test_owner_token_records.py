"""Runtime proof must survive competing startup and belong to one literal bind."""

import json
import multiprocessing
from contextlib import contextmanager

import pytest

from visp_memory.server.owner_token import cleanup_owner_token_files, create_owner_token_files

URL = "http://127.0.0.1:8765"


def _hold_record(directory, ready, stop):
    files = create_owner_token_files(port=8765, url=URL, data_dir=directory, directory=directory)
    ready.set()
    try:
        stop.wait(15)
    finally:
        cleanup_owner_token_files(files)


@contextmanager
def live_record(directory):
    context = multiprocessing.get_context("spawn")
    ready, stop = context.Event(), context.Event()
    child = context.Process(target=_hold_record, args=(directory, ready, stop))
    child.start()
    try:
        assert ready.wait(15)
        yield child
    finally:
        stop.set()
        child.join(3)
        if child.is_alive():
            child.kill()
            child.join(3)
        child.close()


def test_second_startup_preserves_live_record_and_proof(tmp_path):
    with live_record(tmp_path) as child:
        before = {path: path.read_bytes() for path in tmp_path.glob("*") if path.is_file()}
        with pytest.raises(RuntimeError, match=f"{child.pid}"):
            create_owner_token_files(port=8765, url=URL, data_dir=tmp_path / "other",
                                     directory=tmp_path)
        assert all(path.read_bytes() == content for path, content in before.items())


def test_cleanup_keeps_replacement_files(tmp_path):
    files = create_owner_token_files(port=8765, url=URL, data_dir=tmp_path, directory=tmp_path)
    files.token_path.write_text("replacement")
    files.server_path.write_text(json.dumps({"pid": files.pid, "started_at": "later"}))
    cleanup_owner_token_files(files)
    assert files.token_path.read_text() == "replacement"
    assert json.loads(files.server_path.read_text())["started_at"] == "later"


def test_dead_record_is_replaced(tmp_path, monkeypatch):
    (tmp_path / "server-8765.json").write_text(json.dumps({"pid": 12345, "url": URL}))
    monkeypatch.setattr("visp_memory.server.owner_token.pid_is_alive", lambda pid: False,
                        raising=False)
    files = create_owner_token_files(port=8765, url=URL, data_dir=tmp_path, directory=tmp_path)
    try:
        assert json.loads(files.server_path.read_text())["pid"] == files.pid
    finally:
        cleanup_owner_token_files(files)


@pytest.mark.asyncio
async def test_refused_lifespan_preserves_live_files_and_releases_its_role(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from tests.core.test_writer_lock import raw_lock
    from visp_memory.config import MemoryConfig
    from visp_memory.server import app as server_app

    config = MemoryConfig()
    config.storage.data_dir = tmp_path / "other-store"
    config.server.local_owner_mode = True
    config.server.port = 8765
    config.server.host = "127.0.0.1"
    monkeypatch.setattr(server_app, "config", config)
    monkeypatch.setattr("visp_memory.server.owner_token.run_dir", lambda: tmp_path)
    application = SimpleNamespace(state=SimpleNamespace(storage=Mock()))
    with live_record(tmp_path) as child:
        before = {path: path.read_bytes() for path in tmp_path.glob("server-*.json")}
        with pytest.raises(RuntimeError, match=str(child.pid)):
            async with server_app.lifespan(application):
                pytest.fail("a duplicate endpoint must fail before binding")
        assert all(path.read_bytes() == content for path, content in before.items())
        assert len(list(tmp_path.glob("owner-*.token"))) == 1
        application.state.storage.close.assert_called_once()
    with raw_lock(config.storage.data_dir / ".locks/server.lock"):
        pass


def test_live_legacy_record_is_preserved(tmp_path, monkeypatch):
    record = tmp_path / "server-8765.json"
    content = json.dumps({"pid": 12345, "url": URL})
    record.write_text(content)
    token = tmp_path / "owner-8765.token"
    token.write_text("legacy-secret")
    monkeypatch.setattr("visp_memory.server.owner_token.pid_is_alive", lambda pid: True)
    with pytest.raises(RuntimeError, match="12345"):
        create_owner_token_files(port=8765, url=URL, data_dir=tmp_path, directory=tmp_path)
    assert record.read_text() == content
    assert token.read_text() == "legacy-secret"
    assert list(tmp_path.glob("owner-*.token")) == [token]
