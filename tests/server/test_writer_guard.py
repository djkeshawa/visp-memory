"""The serving process, not the importing one, holds the role for the backend lifetime."""

import json
import os
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.core.test_writer_lock import assert_busy, raw_lock
from tests.core.test_writer_lock_processes import memory_config, spawned_role
from visp_memory.core.writer_lock import WriterLockConflict
from visp_memory.server import app as server_app


@pytest.fixture
def served_dir(tmp_path, monkeypatch):
    """Point the module's config at tmp_path and give lifespan a state of its own."""
    monkeypatch.setattr(server_app.config.storage, "data_dir", tmp_path)
    monkeypatch.setattr(server_app.config.server, "host", "::1")
    monkeypatch.setattr(server_app.config.server, "port", 8124)
    monkeypatch.setattr(server_app.config.server, "local_owner_mode", False)
    return tmp_path


def fake_app(storage=None, backend="sqlite"):
    # Not a LocalStorage, so the lifespan starts no dreaming loop against a mock.
    storage = storage or Mock()
    return SimpleNamespace(state=SimpleNamespace(storage=storage, storage_backend=backend))


@pytest.mark.parametrize(
    "backend, constructor",
    [("sqlite", "LocalStorage"), ("arcadedb", "ArcadeDbStorage"), ("neo4j", "LocalStorage")],
)
def test_initialization_only_checks_the_role_and_never_keeps_it(
    tmp_path, monkeypatch, backend, constructor,
):
    config = memory_config(tmp_path)
    config.storage.backend = backend
    config.storage.allow_fallback = True
    config.storage.connect_timeout_seconds = 0
    monkeypatch.setattr(server_app, "Neo4jStorage", Mock(side_effect=RuntimeError("offline")))

    def build(*args, **kwargs):
        # uvicorn's supervisor builds storage too; it must not end up holding the lock.
        with raw_lock(tmp_path / ".locks/server.lock"):
            pass
        return Mock()

    monkeypatch.setattr(server_app, constructor, build)
    storage, effective = server_app.initialize_storage(config)
    assert effective == ("sqlite-fallback" if backend == "neo4j" else backend)
    with raw_lock(tmp_path / ".locks/server.lock"):
        pass
    assert not (tmp_path / ".locks/server.json").exists()


def test_import_time_writes_run_with_the_role_held_then_release_it(tmp_path, monkeypatch):
    """Migration, auth.db and the first administrator must not race another process.

    uvicorn's supervisor imports too, so the role is released once they are done.
    """
    config = memory_config(tmp_path)
    refusals = {}

    def probing_auth_store(path):
        # The last store written at import: another process must be refused here.
        for role in ("server", "local"):
            with spawned_role(tmp_path, role) as (_, conflict):
                refusals[role] = conflict
        return Mock(bootstrap_admin=Mock(return_value=None))

    monkeypatch.setattr(server_app, "AuthStore", probing_auth_store)
    application = SimpleNamespace(state=SimpleNamespace())
    server_app.initialize_app_state(application, config, None, {})
    try:
        assert refusals["server"] and str(os.getpid()) in refusals["server"]
        assert refusals["local"] and str(os.getpid()) in refusals["local"]
        with raw_lock(tmp_path / ".locks/server.lock"):
            pass
        assert not (tmp_path / ".locks/server.json").exists()
    finally:
        application.state.storage.close()


def test_failed_import_time_initialization_releases_the_role(tmp_path, monkeypatch):
    monkeypatch.setattr(server_app, "AuthStore", Mock(side_effect=RuntimeError("auth.db")))
    application = SimpleNamespace(state=SimpleNamespace())
    with pytest.raises(RuntimeError, match="auth.db"):
        server_app.initialize_app_state(application, memory_config(tmp_path), None, {})
    application.state.storage.close()
    with raw_lock(tmp_path / ".locks/server.lock"):
        pass


def test_initialization_is_refused_while_another_process_serves(tmp_path, monkeypatch):
    build = Mock()
    monkeypatch.setattr(server_app, "LocalStorage", build)
    with spawned_role(tmp_path, "server") as (child, _):
        with pytest.raises(WriterLockConflict, match=str(child.pid)):
            server_app.initialize_storage(memory_config(tmp_path))
    build.assert_not_called()


@pytest.mark.asyncio
async def test_lifespan_holds_the_role_while_serving(served_dir):
    async with server_app.lifespan(fake_app()):
        assert_busy(served_dir / ".locks/server.lock")
        metadata = json.loads((served_dir / ".locks/server.json").read_text())
        assert metadata["url"] == "http://[::1]:8124"
        with spawned_role(served_dir, "local") as (_, conflict):
            assert "storage.mode: client" in conflict
    with raw_lock(served_dir / ".locks/server.lock"):
        pass


@pytest.mark.asyncio
async def test_lifespan_reenters_in_process(served_dir):
    """TestClient-style repeated startups in one process must keep working."""
    for _ in range(2):
        async with server_app.lifespan(fake_app()):
            assert_busy(served_dir / ".locks/server.lock")
        with raw_lock(served_dir / ".locks/server.lock"):
            pass


@pytest.mark.asyncio
async def test_lifespan_refuses_a_second_server_without_touching_the_first(served_dir):
    application = fake_app()
    with spawned_role(served_dir, "server") as (child, _):
        with pytest.raises(WriterLockConflict, match=str(child.pid)):
            async with server_app.lifespan(application):
                pytest.fail("a second server must not start")
        application.state.storage.close.assert_not_called()
        assert_busy(served_dir / ".locks/server.lock")


@pytest.mark.asyncio
async def test_neo4j_backend_guards_local_sidecars(served_dir):
    async with server_app.lifespan(fake_app(backend="neo4j")):
        assert_busy(served_dir / ".locks/server.lock")
        with spawned_role(served_dir, "local") as (_, conflict):
            assert conflict


@pytest.mark.asyncio
async def test_lifespan_releases_guard_after_failed_close(served_dir):
    class Storage:
        def close(self):
            assert_busy(served_dir / ".locks/server.lock")
            raise RuntimeError("close failed")

    async with server_app.lifespan(fake_app(Storage())):
        pass
    with raw_lock(served_dir / ".locks/server.lock"):
        pass


@pytest.mark.asyncio
async def test_failed_lifespan_startup_releases_guard(served_dir, monkeypatch):
    application = fake_app(Mock(spec=server_app.LocalStorage))
    monkeypatch.setattr(server_app, "Dreaming", Mock(side_effect=RuntimeError("startup failed")))
    with pytest.raises(RuntimeError, match="startup failed"):
        async with server_app.lifespan(application):
            pytest.fail("startup must fail")
    with raw_lock(served_dir / ".locks/server.lock"):
        pass
    application.state.storage.close.assert_called_once()


@pytest.mark.asyncio
async def test_refusal_by_local_writers_names_them_not_a_second_server(served_dir, caplog):
    with spawned_role(served_dir, "local") as (child, _):
        pid = str(child.pid)
        with pytest.raises(WriterLockConflict, match=pid) as refused:
            async with server_app.lifespan(fake_app()):
                pytest.fail("a server must not start over live local writers")
    assert refused.value.local_pids == (pid,)
    logged = caplog.text
    assert "second server" not in logged
    assert f"pids: {pid}" in logged
    assert "visp-memory connect" in logged


@pytest.mark.asyncio
async def test_refusal_by_another_server_still_says_one_process(served_dir, caplog):
    with spawned_role(served_dir, "server"):
        with pytest.raises(WriterLockConflict):
            async with server_app.lifespan(fake_app()):
                pytest.fail("a second server must not start")
    assert "a second server on the same data directory is refused" in caplog.text


def test_neo4j_import_guards_auth_and_lifecycle(tmp_path, monkeypatch):
    config = memory_config(tmp_path)
    config.storage.backend = "neo4j"
    monkeypatch.setattr(server_app, "Neo4jStorage", Mock(return_value=Mock()))

    def auth(path):
        with spawned_role(tmp_path, "server") as (_, conflict):
            assert conflict
        return Mock(bootstrap_admin=Mock(return_value=None))

    monkeypatch.setattr(server_app, "AuthStore", auth)
    application = SimpleNamespace(state=SimpleNamespace())
    server_app.initialize_app_state(application, config, None, {})
    application.state.storage.close()
    with raw_lock(tmp_path / ".locks/server.lock"):
        pass


def test_single_serve_process_keeps_import_role_until_lifespan(tmp_path, monkeypatch):
    from visp_memory.interfaces.serve_environment import server_environment

    application = SimpleNamespace(state=SimpleNamespace())
    # CLI serve knows whether this process serves or supervises a reload child.
    with server_environment("127.0.0.1", 8123, serving=True):
        server_app.initialize_app_state(application, memory_config(tmp_path), None, {})
    try:
        with spawned_role(tmp_path, "local") as (_, conflict):
            assert conflict
    finally:
        application.state.storage.close()
        application.state.server_import_role.close()
