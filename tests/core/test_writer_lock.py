"""The registry shares OS locks without shortening another handle's lifetime."""

import gc
import json
import os
from contextlib import contextmanager
from datetime import datetime

import pytest

from visp_memory.core import writer_lock
from visp_memory.core.writer_lock import WriterLockConflict, acquire_writer_lock


@contextmanager
def raw_lock(path):
    with path.open("a+b") as stream:
        if os.name == "nt":
            import msvcrt

            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def assert_busy(path):
    with pytest.raises(OSError):
        with raw_lock(path):
            pytest.fail("The last reference must keep its OS lock")


@pytest.mark.parametrize("role", ["local", "server"])
def test_reentry_resolves_paths_and_reference_counts(tmp_path, role):
    first = acquire_writer_lock(tmp_path, role)
    second = acquire_writer_lock(tmp_path / ".." / tmp_path.name, role)
    pattern = "local-*.lock" if role == "local" else "server.lock"
    paths = list((tmp_path / ".locks").glob(pattern))
    assert len(paths) == 1
    try:
        first.release()
        first.release()
        assert_busy(paths[0])
    finally:
        first.release()
        second.release()
    if role == "local":
        assert not paths[0].exists()
    else:
        with raw_lock(paths[0]):
            pass


@pytest.mark.parametrize("roles", [("server", "local"), ("local", "server")])
def test_same_process_can_hold_both_roles(tmp_path, roles):
    first = acquire_writer_lock(tmp_path, roles[0])
    with acquire_writer_lock(tmp_path, roles[1]):
        first.release()
        remaining = (
            next((tmp_path / ".locks").glob("local-*.lock"))
            if roles[1] == "local" else tmp_path / ".locks/server.lock"
        )
        assert_busy(remaining)


def test_server_cleans_stale_local_files(tmp_path):
    locks = tmp_path / ".locks"
    locks.mkdir()
    stale = locks / "local-12345-dead.lock"
    stale.touch()
    with acquire_writer_lock(tmp_path, "server"):
        assert not stale.exists()


def test_server_metadata_is_readable_while_locked_and_removed_on_release(tmp_path):
    with acquire_writer_lock(tmp_path, "server", url="http://127.0.0.1:8123"):
        metadata = json.loads((tmp_path / ".locks/server.json").read_text())
        assert metadata["pid"] == os.getpid()
        assert metadata["url"] == "http://127.0.0.1:8123"
        assert datetime.fromisoformat(metadata["started_at"]).tzinfo is not None
    assert not (tmp_path / ".locks/server.json").exists()


@pytest.mark.parametrize("role", ["local", "server"])
@pytest.mark.parametrize("failure", ["mkdir", "open"])
def test_infrastructure_permission_errors_fail_open(tmp_path, monkeypatch, caplog, role, failure):
    def denied(*args, **kwargs):
        raise PermissionError("read-only fixture")

    if failure == "mkdir":
        monkeypatch.setattr(type(tmp_path), "mkdir", denied)
    else:
        monkeypatch.setattr(os, "open", denied)
    with acquire_writer_lock(tmp_path, role) as handle:
        handle.release()
    assert "read-only fixture" in caplog.text
    assert str(tmp_path) in caplog.text
    assert any(record.levelname == "WARNING" for record in caplog.records)


@pytest.mark.parametrize("role", ["local", "server"])
def test_environment_off_creates_no_files(tmp_path, monkeypatch, role):
    monkeypatch.setenv("VISP_MEMORY_STORAGE_WRITER_GUARD", "off")
    with acquire_writer_lock(tmp_path, role) as handle:
        handle.release()
    assert not (tmp_path / ".locks").exists()


def conflict_message(tmp_path, metadata):
    locks = tmp_path / ".locks"
    locks.mkdir()
    (locks / "server.json").write_text(metadata)
    with raw_lock(locks / "server.lock"):
        with pytest.raises(WriterLockConflict) as caught:
            acquire_writer_lock(tmp_path, "local")
    assert not list(locks.glob("local-*.lock"))
    assert str(tmp_path) in str(caught.value)
    return str(caught.value)


@pytest.mark.parametrize("metadata", ["bad json", "[]"])
def test_unidentified_holder_names_both_possibilities(tmp_path, metadata):
    message = conflict_message(tmp_path, metadata)
    assert "storage.mode: client" in message
    assert "storage.server_url: <server-url>" in message
    assert "visp-memory.yaml" in message
    assert "visp-memory connect" in message
    assert "otherwise wait" in message


def test_busy_server_is_closed_with_actionable_message(tmp_path):
    message = conflict_message(tmp_path, '{"pid": 123, "url": "http://x:8000"}')
    assert "a server (pid: 123) is serving this store at http://x:8000" in message
    assert "storage.mode: client" in message
    assert "storage.server_url: http://x:8000" in message
    assert "visp-memory.yaml" in message
    assert "visp-memory connect" in message
    assert "once available" not in message


def test_offline_maintenance_is_not_described_as_a_server(tmp_path):
    message = conflict_message(tmp_path, '{"pid": 456, "url": null}')
    assert "offline maintenance command (pid: 456)" in message
    assert "wait for it to finish" in message
    assert "served" not in message
    assert "storage.mode" not in message
    assert "once available" not in message


def test_stale_file_that_cannot_be_unlinked_is_treated_as_live(tmp_path, monkeypatch):
    locks = tmp_path / ".locks"
    locks.mkdir()
    stale = locks / "local-12345-dead.lock"
    stale.touch()
    original = type(stale).unlink

    def denied(path, *args, **kwargs):
        if path == stale:
            raise PermissionError("still open on Windows")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(type(stale), "unlink", denied)
    with pytest.raises(WriterLockConflict, match="12345"):
        acquire_writer_lock(tmp_path, "server")
    with raw_lock(locks / "server.lock"):
        pass


def test_detected_live_writer_wins_over_later_infrastructure_error(tmp_path, monkeypatch):
    locks = tmp_path / ".locks"
    locks.mkdir()
    local = locks / "local-12345-live.lock"
    broken = locks / "local-67890-broken.lock"
    local.touch()
    broken.touch()
    original_scan, original_open = os.scandir, os.open

    @contextmanager
    def ordered_scan(path):
        with original_scan(path) as entries:
            yield iter(sorted(entries, key=lambda entry: entry.name))

    def failing_open(path, *args, **kwargs):
        if path == broken:
            raise OSError("filesystem unavailable")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(os, "scandir", ordered_scan)
    monkeypatch.setattr(os, "open", failing_open)
    with raw_lock(local):
        with pytest.raises(WriterLockConflict, match="12345"):
            acquire_writer_lock(tmp_path, "server")


@pytest.mark.skipif(os.name == "nt", reason="Windows refuses unlink while a descriptor is open")
def test_local_repairs_marker_removed_between_create_and_lock(tmp_path, monkeypatch):
    original = writer_lock._open_locked
    removed = False

    def scanned_before_lock(path, data_dir):
        nonlocal removed
        fd = original(path, data_dir)
        if path.name.startswith("local-") and not removed:
            removed = True
            path.unlink()
        return fd

    monkeypatch.setattr(writer_lock, "_open_locked", scanned_before_lock)
    with acquire_writer_lock(tmp_path, "local"):
        assert removed
        assert_busy(next((tmp_path / ".locks").glob("local-*.lock")))


def test_metadata_write_failure_keeps_guard(tmp_path, monkeypatch, caplog):
    def denied(*args, **kwargs):
        raise PermissionError("metadata is read-only")

    monkeypatch.setattr(type(tmp_path), "write_text", denied)
    with acquire_writer_lock(tmp_path, "server"):
        assert_busy(tmp_path / ".locks/server.lock")
        assert "metadata is read-only" in caplog.text


def test_discarded_handle_releases_its_reference(tmp_path):
    first = acquire_writer_lock(tmp_path, "server")
    second = acquire_writer_lock(tmp_path, "server")
    del first
    gc.collect()
    assert_busy(tmp_path / ".locks/server.lock")
    second.release()
    with raw_lock(tmp_path / ".locks/server.lock"):
        pass
