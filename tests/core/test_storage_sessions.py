from visp_memory.core.storage import LocalStorage, SessionCompletionStatus


def test_local_session_is_bound_and_completes_once(tmp_path):
    storage = LocalStorage(tmp_path)

    session_id = storage.start_session(
        owner_id="alice",
        team_id="team-alpha",
        repo_id="repo-a",
    )

    session = storage.get_session(session_id)
    assert session["id"] == session_id
    assert session["owner_id"] == "alice"
    assert session["team_id"] == "team-alpha"
    assert session["repo_id"] == "repo-a"
    assert session["summary"] is None
    assert session["memory_ids"] == []
    assert session["started_at"]
    assert session["ended_at"] is None
    assert (
        storage.end_session(session_id, "Done", ["memory-1"])
        is SessionCompletionStatus.COMPLETED
    )
    assert (
        storage.end_session(session_id, "Again", [])
        is SessionCompletionStatus.ALREADY_COMPLETED
    )
    assert (
        storage.end_session("missing", "", [])
        is SessionCompletionStatus.NOT_FOUND
    )


def test_schema_v4_migration_adds_nullable_session_scope(tmp_path):
    data_dir = tmp_path / "data"
    storage = LocalStorage(data_dir)
    session_id = storage.start_session()
    storage.close()

    import sqlite3

    with sqlite3.connect(data_dir / "memories.db") as conn:
        conn.execute("DELETE FROM schema_migrations WHERE version = 5")
        conn.execute("INSERT OR IGNORE INTO schema_migrations(version) VALUES (4)")
        conn.execute("DROP INDEX IF EXISTS idx_sessions_owner")
        conn.execute("DROP INDEX IF EXISTS idx_sessions_repo")
        conn.execute("ALTER TABLE sessions DROP COLUMN owner_id")
        conn.execute("ALTER TABLE sessions DROP COLUMN team_id")
        conn.execute("ALTER TABLE sessions DROP COLUMN repo_id")
        conn.commit()

    backup = tmp_path / "backup.db"
    report = LocalStorage.migrate_schema(data_dir, backup_path=backup)

    assert report == {"from_version": 4, "to_version": 5, "status": "migrated"}
    migrated = LocalStorage(data_dir)
    assert migrated.get_session(session_id)["owner_id"] is None
    assert migrated.get_session(session_id)["repo_id"] is None
