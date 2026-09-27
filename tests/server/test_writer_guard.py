"""Server construction and shutdown bracket the complete backend lifetime."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.core.test_writer_lock import assert_busy, raw_lock
from tests.core.test_writer_lock_processes import memory_config, spawned_role
from visp_memory.core.writer_lock import acquire_writer_lock
from visp_memory.server import app as server_app


@pytest.mark.parametrize(
    "backend, constructor",
    [("sqlite", "LocalStorage"), ("arcadedb", "ArcadeDbStorage"), ("neo4j", "LocalStorage")],
)
def test_initialization_guards_local_backends_and_fallback(
    tmp_path, monkeypatch, backend, constructor,
):
    config = memory_config(tmp_path)
    config.storage.backend = backend
    config.storage.allow_fallback = True
    config.storage.connect_timeout_seconds = 0
    config.server.host = "::1" if backend == "arcadedb" else "127.0.0.1"
    config.server.port = 8124
    fake_app = SimpleNamespace(state=SimpleNamespace())
    monkeypatch.setattr(server_app, "app", fake_app)
    monkeypatch.setattr(server_app, "Neo4jStorage", Mock(side_effect=RuntimeError("offline")))

    def build(*args, **kwargs):
        assert_busy(tmp_path / ".locks/server.lock")
        return Mock()

    monkeypatch.setattr(server_app, constructor, build)
    storage, effective = server_app.initialize_storage(config)
    try:
        assert effective == ("sqlite-fallback" if backend == "neo4j" else backend)
        metadata = json.loads((tmp_path / ".locks/server.json").read_text())
        host = "[::1]" if backend == "arcadedb" else "127.0.0.1"
        assert metadata["url"] == f"http://{host}:8124"
        with spawned_role(tmp_path, "local") as (_, conflict):
            assert "storage.mode: client" in conflict
    finally:
        storage.close()
        fake_app.state.writer_lock.release()


def test_failed_initialization_releases_server_guard(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        assert_busy(tmp_path / ".locks/server.lock")
        raise RuntimeError("backend unavailable")

    monkeypatch.setattr(server_app, "LocalStorage", fail)
    with pytest.raises(RuntimeError, match="backend unavailable"):
        server_app.initialize_storage(memory_config(tmp_path))
    with raw_lock(tmp_path / ".locks/server.lock"):
        pass


@pytest.mark.asyncio
async def test_lifespan_releases_guard_after_failed_close(tmp_path):
    class Storage:
        def close(self):
            assert_busy(tmp_path / ".locks/server.lock")
            raise RuntimeError("close failed")

    handle = acquire_writer_lock(tmp_path, "server")
    fake_app = SimpleNamespace(state=SimpleNamespace(storage=Storage(), writer_lock=handle))
    try:
        async with server_app.lifespan(fake_app):
            pass
        with raw_lock(tmp_path / ".locks/server.lock"):
            pass
    finally:
        handle.release()


@pytest.mark.asyncio
async def test_failed_lifespan_startup_releases_guard(tmp_path, monkeypatch):
    storage = Mock(spec=server_app.LocalStorage)
    handle = acquire_writer_lock(tmp_path, "server")
    fake_app = SimpleNamespace(state=SimpleNamespace(storage=storage, writer_lock=handle))
    monkeypatch.setattr(server_app, "Dreaming", Mock(side_effect=RuntimeError("startup failed")))
    try:
        with pytest.raises(RuntimeError, match="startup failed"):
            async with server_app.lifespan(fake_app):
                pytest.fail("startup must fail")
        with raw_lock(tmp_path / ".locks/server.lock"):
            pass
        storage.close.assert_called_once()
    finally:
        handle.release()
