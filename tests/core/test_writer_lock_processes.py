"""Spawn avoids inherited locks and exercises the Windows process model too."""

import multiprocessing
import os
from contextlib import contextmanager

import pytest

from visp_memory import Memory, MemoryConfig
from visp_memory.core.writer_lock import WriterLockConflict, acquire_writer_lock

TIMEOUT = 15
SERVER_URL = "http://127.0.0.1:8123"


def hold_role(data_dir, role, ready, stop, result):
    try:
        with acquire_writer_lock(data_dir, role, url=SERVER_URL if role == "server" else None):
            result.send(None)
            ready.set()
            if not stop.wait(TIMEOUT):
                raise TimeoutError("parent did not release the child")
    except WriterLockConflict as error:
        result.send(str(error))
        ready.set()
    finally:
        result.close()


@contextmanager
def spawned_role(data_dir, role, *, context=None):
    context = context or multiprocessing.get_context("spawn")
    ready, stop = context.Event(), context.Event()
    receive, send = context.Pipe(duplex=False)
    child = context.Process(target=hold_role, args=(data_dir, role, ready, stop, send))
    child.start()
    send.close()
    try:
        assert ready.wait(TIMEOUT), "child did not announce its result"
        assert receive.poll(TIMEOUT)
        yield child, receive.recv()
    finally:
        # A killed Event waiter cannot acknowledge Condition.notify_all().
        if child.is_alive():
            stop.set()
        child.join(3)
        if child.is_alive():
            child.terminate()
            child.join(3)
        if child.is_alive():
            child.kill()
            child.join(3)
        receive.close()
        child.close()


def memory_config(data_dir):
    config = MemoryConfig()
    config.storage.data_dir = data_dir
    config.storage.mode = "local"
    config.storage.backend = "sqlite"
    config.embedding.provider = "noop"
    return config


def test_server_blocks_memory_and_client_mode_does_not_lock(tmp_path):
    with spawned_role(tmp_path, "server") as (child, conflict):
        assert conflict is None
        with pytest.raises(WriterLockConflict) as caught:
            Memory(config=memory_config(tmp_path))
        assert str(child.pid) in str(caught.value)
        assert SERVER_URL in str(caught.value)
        assert "storage.mode: client" in str(caught.value)
        assert "storage.server_url:" in str(caught.value)
        assert not (tmp_path / "memories.db").exists()
        config = memory_config(tmp_path)
        config.storage.mode = "client"
        with Memory(config=config):
            assert not list((tmp_path / ".locks").glob("local-*.lock"))


def test_local_memory_blocks_server_until_last_close(tmp_path):
    first = Memory(config=memory_config(tmp_path))
    second = Memory(config=memory_config(tmp_path))
    try:
        first.close()
        first.close()
        with spawned_role(tmp_path, "server") as (_, conflict):
            assert str(os.getpid()) in conflict
            assert str(tmp_path) in conflict
    finally:
        first.close()
        second.close()
    with spawned_role(tmp_path, "server") as (_, conflict):
        assert conflict is None


def test_killed_server_releases_os_lock(tmp_path):
    with spawned_role(tmp_path, "server") as (child, conflict):
        assert conflict is None
        child.kill()
        child.join(3)
        assert not child.is_alive()
        with acquire_writer_lock(tmp_path, "server"):
            pass


def test_two_local_processes_coexist(tmp_path):
    with Memory(config=memory_config(tmp_path)):
        with spawned_role(tmp_path, "local") as (_, conflict):
            assert conflict is None
            assert len(list((tmp_path / ".locks").glob("local-*.lock"))) == 2


def test_second_server_is_refused(tmp_path):
    with spawned_role(tmp_path, "server") as (child, conflict):
        assert conflict is None
        with pytest.raises(WriterLockConflict, match=str(child.pid)):
            acquire_writer_lock(tmp_path, "server")


def test_environment_off_bypasses_live_server(tmp_path, monkeypatch):
    with spawned_role(tmp_path, "server") as (_, conflict):
        assert conflict is None
        monkeypatch.setenv("VISP_MEMORY_STORAGE_WRITER_GUARD", "off")
        with acquire_writer_lock(tmp_path, "local"):
            pass


@pytest.mark.skipif("fork" not in multiprocessing.get_all_start_methods(), reason="POSIX only")
def test_fork_does_not_reuse_parent_registry_or_unlock_parent(tmp_path):
    context = multiprocessing.get_context("fork")
    with acquire_writer_lock(tmp_path, "server"):
        with spawned_role(tmp_path, "local", context=context) as (_, msg):
            assert str(os.getpid()) in msg
        with spawned_role(tmp_path, "local") as (_, msg):
            assert str(os.getpid()) in msg
