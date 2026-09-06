import json
import sqlite3

import pytest

from visp_memory.core.maintenance import backup_storage, upgrade_storage
from visp_memory.core.storage import LocalStorage


def test_backup_preserves_all_databases_and_wal(tmp_path):
    source = tmp_path / "data"
    LocalStorage(source).store_memory("A durable fact", auto_link=False)
    with sqlite3.connect(source / "auth.db") as auth:
        auth.execute("PRAGMA journal_mode=WAL")
        auth.execute("CREATE TABLE accounts (name TEXT)")
        auth.execute("INSERT INTO accounts VALUES ('example')")
        auth.commit()
        backup_storage(source, tmp_path / "backup")
    with sqlite3.connect(tmp_path / "backup" / "auth.db") as copy:
        assert copy.execute("SELECT name FROM accounts").fetchone() == ("example",)
    manifest = json.loads((tmp_path / "backup" / "manifest.json").read_text())
    assert {f["path"] for f in manifest["files"]} >= {"auth.db", "memories.db"}
    assert not list((tmp_path / "backup").glob("*-wal"))


def test_backup_refuses_existing_destination_and_recursive_copy(tmp_path):
    source = tmp_path / "data"
    LocalStorage(source)
    with pytest.raises(ValueError):
        backup_storage(source, source / "backups")
    destination = tmp_path / "existing"
    destination.mkdir()
    (destination / "keep").write_text("retain")
    with pytest.raises(FileExistsError):
        backup_storage(source, destination)
    assert (destination / "keep").read_text() == "retain"


def test_current_upgrade_does_not_create_unnecessary_backup(tmp_path):
    source = tmp_path / "data"
    LocalStorage(source)
    assert upgrade_storage(source, tmp_path / "backup")["status"] == "already_current"
    assert not (tmp_path / "backup").exists()


def test_upgrade_keeps_auth_and_original_schema_in_full_backup(tmp_path):
    source = tmp_path / "data"
    store = LocalStorage(source)
    memory_id = store.store_memory("Keep this knowledge", auto_link=False)
    session_id = store.start_session()
    store.close()
    (source / "auth.db").write_bytes(b"credential-store-fixture")
    with sqlite3.connect(source / "memories.db") as db:
        db.execute("DELETE FROM schema_migrations WHERE version = 5")
        db.execute("INSERT OR IGNORE INTO schema_migrations(version) VALUES (4)")
        db.execute("DROP INDEX IF EXISTS idx_sessions_owner")
        db.execute("DROP INDEX IF EXISTS idx_sessions_repo")
        for column in ("owner_id", "team_id", "repo_id"):
            db.execute(f"ALTER TABLE sessions DROP COLUMN {column}")
    destination = tmp_path / "backup"
    result = upgrade_storage(source, destination)
    assert result["status"] == "migrated"
    assert result["from_version"] == 4
    assert (destination / "auth.db").read_bytes() == b"credential-store-fixture"
    with sqlite3.connect(destination / "memories.db") as original:
        assert original.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 4
    upgraded = LocalStorage(source)
    assert upgraded.get_memory(memory_id)["content"] == "Keep this knowledge"
    assert upgraded.get_session(session_id)["repo_id"] is None


def test_failed_backup_preserves_source_and_removes_partial_copy(tmp_path, monkeypatch):
    import visp_memory.core.maintenance as maintenance

    source = tmp_path / "data"
    store = LocalStorage(source)
    memory_id = store.store_memory("Keep this knowledge", auto_link=False)

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(maintenance, "_digest", fail)
    with pytest.raises(OSError, match="disk full"):
        backup_storage(source, tmp_path / "partial")
    assert not (tmp_path / "partial").exists()
    assert store.get_memory(memory_id)["content"] == "Keep this knowledge"
