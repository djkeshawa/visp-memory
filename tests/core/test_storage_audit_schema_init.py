"""First open of a SQLite store is one transaction (audit regression)."""

import sqlite3
import subprocess
import sys
import threading
import time

import pytest

from visp_memory.core.storage import (
    STORAGE_SCHEMA_VERSION,
    LocalStorage,
    StorageMigrationRequired,
)

_OPENER = """
import sys
from visp_memory.core.storage import LocalStorage
print("ready", flush=True)
sys.stdin.readline()
try:
    LocalStorage(sys.argv[1])
    print("ok", flush=True)
except Exception as exc:
    print(f"{type(exc).__name__}: {exc}", flush=True)
"""


def _tables(db_path):
    with sqlite3.connect(db_path) as conn:
        return {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }


def _versions(db_path):
    with sqlite3.connect(db_path) as conn:
        return [row[0] for row in conn.execute("SELECT version FROM schema_migrations")]


def test_concurrent_first_opens_in_separate_processes_all_succeed(tmp_path):
    trials, openers = 4, 4
    processes = []
    for trial in range(trials):
        data_dir = tmp_path / f"store-{trial}"
        data_dir.mkdir()
        for _ in range(openers):
            processes.append(
                subprocess.Popen(
                    [sys.executable, "-c", _OPENER, str(data_dir)],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    text=True,
                )
            )
    try:
        # Every opener has imported the package before any of them starts, so
        # the first opens genuinely race.
        for process in processes:
            assert process.stdout.readline().strip() == "ready"
        for process in processes:
            process.stdin.write("go\n")
            process.stdin.flush()
        results = [process.communicate(timeout=60)[0].strip() for process in processes]
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()

    assert results == ["ok"] * len(processes)
    for trial in range(trials):
        db_path = tmp_path / f"store-{trial}" / "memories.db"
        assert _versions(db_path) == [STORAGE_SCHEMA_VERSION]
        LocalStorage(db_path.parent)


def test_second_open_waits_for_an_in_progress_first_open(tmp_path, monkeypatch):
    original = LocalStorage._register_repositories_for_existing_scopes
    first_open_inside = threading.Event()

    def slow(conn):
        if threading.current_thread() is not threading.main_thread():
            first_open_inside.set()
            time.sleep(0.5)
        original(conn)

    monkeypatch.setattr(
        LocalStorage, "_register_repositories_for_existing_scopes", staticmethod(slow)
    )
    errors = []

    def first_open():
        try:
            LocalStorage(tmp_path)
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(exc)

    worker = threading.Thread(target=first_open)
    worker.start()
    assert first_open_inside.wait(10)
    # Every table but the version row exists at this point; before the fix the
    # DDL had autocommitted and this open was refused as unversioned storage.
    LocalStorage(tmp_path)
    worker.join()

    assert errors == []
    assert _versions(tmp_path / "memories.db") == [STORAGE_SCHEMA_VERSION]


def test_failed_first_open_leaves_no_partial_schema(tmp_path, monkeypatch):
    def interrupted(conn):
        raise RuntimeError("interrupted after the DDL, before the version row")

    monkeypatch.setattr(
        LocalStorage,
        "_register_repositories_for_existing_scopes",
        staticmethod(interrupted),
    )
    with pytest.raises(RuntimeError, match="interrupted"):
        LocalStorage(tmp_path)

    assert _tables(tmp_path / "memories.db") == set()

    monkeypatch.undo()
    storage = LocalStorage(tmp_path)
    assert _versions(storage.db_path) == [STORAGE_SCHEMA_VERSION]
    assert storage.store_memory("works", repo_id="repo-a", auto_link=False)


def test_new_store_creates_intents_repo_id_without_alter(tmp_path):
    storage = LocalStorage(tmp_path)
    with sqlite3.connect(storage.db_path) as conn:
        (intents_sql,) = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'intents'"
        ).fetchone()
        indexes = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' "
                "AND tbl_name = 'intents'"
            )
        }
    assert "repo_id TEXT DEFAULT NULL" in intents_sql
    assert "idx_intents_repo" in indexes


def _abandon_first_open(data_dir):
    """Reproduce what a pre-transactional first open left behind."""
    LocalStorage(data_dir)
    with sqlite3.connect(data_dir / "memories.db") as conn:
        conn.execute("DELETE FROM schema_migrations")
        conn.execute("DROP TABLE recall_events")
        conn.execute("DROP TABLE audit_logs")


def test_empty_abandoned_first_open_is_completed(tmp_path):
    _abandon_first_open(tmp_path)

    storage = LocalStorage(tmp_path)

    assert _versions(storage.db_path) == [STORAGE_SCHEMA_VERSION]
    assert {"recall_events", "audit_logs"} <= _tables(storage.db_path)


def test_abandoned_store_holding_rows_is_still_refused(tmp_path):
    _abandon_first_open(tmp_path)
    with sqlite3.connect(tmp_path / "memories.db") as conn:
        conn.execute("INSERT INTO repositories (id, name) VALUES ('repo-a', 'repo-a')")

    with pytest.raises(StorageMigrationRequired, match="Unversioned"):
        LocalStorage(tmp_path)
