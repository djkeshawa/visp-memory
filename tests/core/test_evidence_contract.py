import hashlib
import shutil
import sqlite3

import pytest

from visp_memory import Memory, MemoryConfig
from visp_memory.core.storage import (
    STORAGE_SCHEMA_VERSION,
    UNSCOPED_REPO_ID,
    EvidenceError,
    EvidenceImmutableError,
    EvidenceReferenceError,
    LocalStorage,
    StorageMigrationRequired,
)
from visp_memory.core.trust import Provenance, provenance_of, provenance_tag
from visp_memory.layers.semantic import SemanticMemory


def _create_v2_fixture(path, *, status="superseded"):
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)"
        )
        conn.execute("INSERT INTO schema_migrations(version) VALUES (2)")
        conn.execute(
            """
            CREATE TABLE memories (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                layer TEXT NOT NULL,
                category TEXT,
                importance REAL,
                repo_id TEXT,
                access_count INTEGER,
                tags TEXT,
                metadata TEXT,
                source_ids TEXT,
                status TEXT,
                approved_by TEXT,
                approved_at TEXT,
                archived_at TEXT,
                source TEXT,
                quality_flags TEXT,
                last_quality_checked_at TEXT,
                created_at TEXT,
                accessed_at TEXT,
                compressed_at TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO memories (
                id, content, layer, category, importance, repo_id, access_count,
                tags, metadata, source_ids, status, source, quality_flags,
                created_at, accessed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-belief",
                "Legacy authentication belief",
                "semantic",
                "fact",
                0.8,
                "repo-a",
                3,
                '["provenance:authored", "security"]',
                '{"valid_from":"2025-01-01T00:00:00+00:00"}',
                '["legacy-source"]',
                status,
                "authored",
                "[]",
                "2025-01-02T00:00:00+00:00",
                "2025-01-03T00:00:00+00:00",
            ),
        )


def _create_unversioned_fixture(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE memories (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                layer TEXT NOT NULL,
                repo_id TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO memories(id, content, layer, repo_id) VALUES (?, ?, ?, ?)",
            ("unversioned-memory", "Preserve this row", "episodic", "repo-a"),
        )


def _create_v2_unscoped_graph_fixture(path):
    _create_v2_fixture(path, status="active")
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE memories SET repo_id = NULL, source_ids = ? WHERE id = ?",
            ('["legacy-episode"]', "legacy-belief"),
        )
        conn.execute(
            """
            INSERT INTO memories (
                id, content, layer, category, importance, repo_id, access_count,
                tags, metadata, source_ids, status, source, quality_flags,
                created_at, accessed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-episode",
                "Legacy unscoped observation",
                "episodic",
                "observation",
                0.7,
                "   ",
                0,
                "[]",
                "{}",
                "[]",
                "active",
                "authored",
                "[]",
                "2025-01-01T00:00:00+00:00",
                "2025-01-01T00:00:00+00:00",
            ),
        )
        conn.execute(
            """
            CREATE TABLE intents (
                id TEXT PRIMARY KEY,
                description TEXT NOT NULL,
                priority INTEGER,
                status TEXT,
                context TEXT,
                created_at TEXT,
                updated_at TEXT,
                repo_id TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO intents VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "legacy-intent",
                "Preserve unscoped migration",
                1,
                "active",
                "{}",
                "2025-01-01T00:00:00+00:00",
                "2025-01-01T00:00:00+00:00",
                "",
            ),
        )
        conn.execute(
            """
            CREATE TABLE relationships (
                id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                relationship TEXT NOT NULL,
                strength REAL,
                created_at TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO relationships VALUES (?, ?, ?, ?, ?, ?)",
            (
                "legacy-support",
                "legacy-episode",
                "legacy-belief",
                "supports",
                0.8,
                "2025-01-02T00:00:00+00:00",
            ),
        )


def _create_malformed_v3_fixture(path, defect):
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO schema_migrations VALUES (3)")
        conn.execute(
            "CREATE TABLE memories "
            "(id TEXT PRIMARY KEY, content TEXT, layer TEXT, repo_id TEXT)"
        )
        conn.execute(
            "INSERT INTO memories VALUES (?, ?, ?, ?)",
            ("belief-1", "Declared current belief", "semantic", "repo-a"),
        )
        if defect != "missing_evidence_table":
            content_hash_column = (
                ""
                if defect == "missing_evidence_column"
                else "content_hash TEXT NOT NULL,"
            )
            conn.execute(
                f"""
                CREATE TABLE evidence (
                    id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    {content_hash_column}
                    repo_id TEXT,
                    evidence_type TEXT NOT NULL,
                    provenance TEXT NOT NULL,
                    metadata TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            if defect == "missing_evidence_column":
                conn.execute(
                    "INSERT INTO evidence VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        "ev-1",
                        "Exact observation",
                        "repo-a",
                        "observation",
                        "unknown",
                        "{}",
                        "2025-01-01T00:00:00+00:00",
                    ),
                )
            else:
                content_hash = hashlib.sha256(b"Exact observation").hexdigest()
                if defect == "invalid_hash":
                    content_hash = "not-the-content-hash"
                conn.execute(
                    "INSERT INTO evidence VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        "ev-1",
                        "Exact observation",
                        content_hash,
                        "repo-a",
                        "observation",
                        "unknown",
                        "{}",
                        "2025-01-01T00:00:00+00:00",
                    ),
                )
        foreign_keys = "" if defect == "missing_foreign_keys" else (
            ", FOREIGN KEY (belief_id) REFERENCES memories(id) ON DELETE CASCADE"
            ", FOREIGN KEY (evidence_id) REFERENCES evidence(id) ON DELETE RESTRICT"
        )
        conn.execute(
            "CREATE TABLE belief_evidence ("
            "belief_id TEXT NOT NULL, evidence_id TEXT NOT NULL, created_at TEXT NOT NULL, "
            f"PRIMARY KEY (belief_id, evidence_id){foreign_keys})"
        )
        if defect not in {
            "missing_evidence_table",
            "missing_evidence_column",
            "missing_link",
        }:
            conn.execute(
                "INSERT INTO belief_evidence VALUES (?, ?, ?)",
                ("belief-1", "ev-1", "2025-01-01T00:00:00+00:00"),
            )


def _sqlite_snapshot(path):
    with sqlite3.connect(path) as conn:
        schema = conn.execute(
            "SELECT type, name, sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        ).fetchall()
        memories = conn.execute("SELECT * FROM memories ORDER BY id").fetchall()
    return schema, memories


def test_sqlite_stores_immutable_evidence_separately_and_links_belief(tmp_path):
    storage = LocalStorage(tmp_path)
    evidence_id = storage.store_evidence(
        "Observed auth response requires a secure cookie",
        repo_id="repo-a",
        evidence_type="tool_output",
        provenance="derived",
        metadata={"tool": "pytest"},
    )

    belief_id = storage.store_memory(
        "Authentication requires secure cookies",
        layer="semantic",
        repo_id="repo-a",
        evidence_ids=[evidence_id],
        auto_link=False,
    )

    evidence = storage.get_evidence(evidence_id)
    belief = storage.get_memory(belief_id)
    assert evidence["content_hash"] == hashlib.sha256(
        evidence["content"].encode("utf-8")
    ).hexdigest()
    assert evidence["record_type"] == "evidence"
    assert belief["evidence_ids"] == [evidence_id]
    assert evidence_id not in {item["id"] for item in storage.list_memories(repo_id="repo-a")}


def test_semantic_facade_refuses_belief_without_evidence(tmp_path):
    storage = LocalStorage(tmp_path)

    with pytest.raises(EvidenceReferenceError, match="requires at least one evidence"):
        SemanticMemory(storage).establish(
            "Authentication requires secure cookies", repo_id="repo-a"
        )

    assert storage.list_memories(repo_id="repo-a") == []


def test_memory_learn_stores_exact_caller_input_as_provenance_evidence(tmp_path):
    config = MemoryConfig(repo_id="repo-a")
    config.storage.data_dir = tmp_path
    config.embedding.provider = "noop"
    memory = Memory(config=config)

    belief_id = memory.learn("Authentication requires secure cookies")

    belief = memory._storage.get_memory(belief_id)
    evidence = memory._storage.get_evidence(belief["evidence_ids"][0])
    assert evidence["content"] == "Authentication requires secure cookies"
    assert evidence["evidence_type"] == "caller_input"
    assert evidence["provenance"] == "unknown"


@pytest.mark.parametrize("bad_reference", ["missing", "wrong_kind", "deleted", "self"])
def test_sqlite_rejects_invalid_evidence_atomically(tmp_path, bad_reference):
    storage = LocalStorage(tmp_path)
    content = f"Belief rejected for {bad_reference} evidence"
    belief_id = storage._generate_id(content)

    if bad_reference == "wrong_kind":
        evidence_id = storage.store_memory(
            "An episodic compatibility memory",
            layer="episodic",
            repo_id="repo-a",
            auto_link=False,
        )
    elif bad_reference == "deleted":
        evidence_id = storage.store_evidence("Temporary evidence", repo_id="repo-a")
        with storage._get_db() as conn:
            conn.execute("DELETE FROM evidence WHERE id = ?", (evidence_id,))
            conn.commit()
    elif bad_reference == "self":
        evidence_id = belief_id
    else:
        evidence_id = "missing-evidence"

    with pytest.raises(EvidenceReferenceError):
        storage.store_memory(
            content,
            layer="semantic",
            repo_id="repo-a",
            evidence_ids=[evidence_id],
            auto_link=False,
        )

    assert storage.get_memory(belief_id) is None


def test_sqlite_rejects_cross_repo_evidence_and_lineage_atomically(tmp_path):
    storage = LocalStorage(tmp_path)
    evidence_id = storage.store_evidence("Repository B evidence", repo_id="repo-b")
    source_id = storage.store_memory(
        "Repository B episode", layer="episodic", repo_id="repo-b", auto_link=False
    )

    for kwargs in ({"evidence_ids": [evidence_id]}, {"source_ids": [source_id]}):
        with pytest.raises(EvidenceReferenceError, match="same repository"):
            storage.store_memory(
                "Repository A belief",
                layer="semantic",
                repo_id="repo-a",
                auto_link=False,
                **kwargs,
            )

    assert storage.list_memories(layer="semantic", repo_id="repo-a") == []


@pytest.mark.parametrize("bad_lineage", ["missing", "deleted", "cross_repo"])
def test_sqlite_rejects_invalid_lineage_even_with_direct_evidence(
    tmp_path, bad_lineage
):
    storage = LocalStorage(tmp_path)
    evidence_id = storage.store_evidence("Direct repository A evidence", repo_id="repo-a")
    if bad_lineage == "missing":
        source_id = "missing-source"
    else:
        source_repo = "repo-b" if bad_lineage == "cross_repo" else "repo-a"
        source_id = storage.store_memory(
            f"{bad_lineage} lineage source",
            repo_id=source_repo,
            auto_link=False,
        )
        if bad_lineage == "cross_repo":
            with storage._get_db() as conn:
                conn.execute("DELETE FROM belief_evidence WHERE belief_id = ?", (source_id,))
                conn.commit()
        if bad_lineage == "deleted":
            storage.update_memory(source_id, status="deleted")

    with pytest.raises(EvidenceReferenceError):
        storage.store_memory(
            f"Belief rejected for {bad_lineage} lineage",
            layer="semantic",
            repo_id="repo-a",
            evidence_ids=[evidence_id],
            source_ids=[source_id],
            auto_link=False,
        )

    assert storage.list_memories(layer="semantic", repo_id="repo-a") == []


def test_evidence_update_and_id_collision_refuse_without_mutation(tmp_path):
    storage = LocalStorage(tmp_path)
    evidence_id = storage.store_evidence(
        "Immutable evidence", repo_id="repo-a", evidence_id="evidence-stable"
    )
    original = storage.get_evidence(evidence_id)

    assert storage.store_evidence(
        "Immutable evidence", repo_id="repo-a", evidence_id="evidence-stable"
    ) == evidence_id
    with pytest.raises(EvidenceImmutableError):
        storage.update_evidence(evidence_id, content="Changed evidence")
    with pytest.raises(EvidenceImmutableError, match="collision"):
        storage.store_evidence(
            "Different evidence", repo_id="repo-a", evidence_id="evidence-stable"
        )

    assert storage.get_evidence(evidence_id) == original


def test_evidence_idempotency_compares_only_explicit_created_at(tmp_path):
    storage = LocalStorage(tmp_path)
    first_created_at = "2026-01-01T00:00:00+00:00"
    storage.store_evidence(
        "Timestamped evidence",
        repo_id="repo-a",
        evidence_id="evidence-timestamped",
        created_at=first_created_at,
    )

    assert storage.store_evidence(
        "Timestamped evidence",
        repo_id="repo-a",
        evidence_id="evidence-timestamped",
    ) == "evidence-timestamped"
    with pytest.raises(EvidenceImmutableError, match="collision"):
        storage.store_evidence(
            "Timestamped evidence",
            repo_id="repo-a",
            evidence_id="evidence-timestamped",
            created_at="2026-01-02T00:00:00+00:00",
        )

    assert storage.get_evidence("evidence-timestamped")["created_at"] == first_created_at


def test_local_evidence_redacts_secret_before_hash_and_persistence(tmp_path):
    storage = LocalStorage(tmp_path)
    secret = "sk-proj-abcdefghijklmnopqrstuvwxyz123456"

    evidence_id = storage.store_evidence(
        f"Observed credential {secret}", repo_id="repo-a"
    )

    evidence = storage.get_evidence(evidence_id)
    assert secret not in evidence["content"]
    assert "[REDACTED:openai-key]" in evidence["content"]
    assert evidence["content_hash"] == LocalStorage._evidence_hash(evidence["content"])


def test_v2_migration_is_explicit_idempotent_and_preserves_quarantined_legacy_state(tmp_path):
    data_dir = tmp_path / "legacy"
    db_path = data_dir / "memories.db"
    _create_v2_fixture(db_path)
    source_copy = tmp_path / "source-v2.db"
    shutil.copy2(db_path, source_copy)
    backup = tmp_path / "rollback-v2.db"

    with pytest.raises(StorageMigrationRequired):
        LocalStorage(data_dir)

    report = LocalStorage.migrate_schema(data_dir, backup_path=backup)
    assert report == {"from_version": 2, "to_version": STORAGE_SCHEMA_VERSION, "status": "migrated"}
    with sqlite3.connect(backup) as backup_conn, sqlite3.connect(source_copy) as source_conn:
        assert backup_conn.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ).fetchone() == source_conn.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ).fetchone() == (2,)
        assert backup_conn.execute(
            "SELECT id, content, status, source_ids FROM memories"
        ).fetchall() == source_conn.execute(
            "SELECT id, content, status, source_ids FROM memories"
        ).fetchall()

    storage = LocalStorage(data_dir)
    belief = storage.get_memory("legacy-belief")
    assert belief["status"] == "superseded"
    assert belief["source_ids"] == ["legacy-source"]
    assert belief["evidence_ids"]
    assert provenance_of(belief) is Provenance.UNKNOWN
    assert "legacy_unreviewed" in belief["quality_flags"]
    evidence = storage.get_evidence(belief["evidence_ids"][0])
    assert evidence["evidence_type"] == "legacy_import"
    assert evidence["provenance"] == "unknown"
    assert evidence["content"] == belief["content"]

    before = storage.list_evidence(repo_id="repo-a")
    assert LocalStorage.migrate_schema(data_dir, backup_path=tmp_path / "retry.db") == {
        "from_version": STORAGE_SCHEMA_VERSION,
        "to_version": STORAGE_SCHEMA_VERSION,
        "status": "already_current",
    }
    assert LocalStorage(data_dir).list_evidence(repo_id="repo-a") == before


def test_v2_migration_refuses_secret_before_backup_or_schema_write(tmp_path):
    data_dir = tmp_path / "legacy-secret"
    db_path = data_dir / "memories.db"
    backup_path = tmp_path / "must-not-exist.db"
    secret = "sk-proj-abcdefghijklmnopqrstuvwxyz123456"
    _create_v2_fixture(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE memories SET content = ? WHERE id = ?",
            (f"Legacy secret {secret}", "legacy-belief"),
        )
    bytes_before = db_path.read_bytes()
    snapshot_before = _sqlite_snapshot(db_path)

    with pytest.raises(EvidenceError, match="secret-bearing legacy content"):
        LocalStorage.migrate_schema(data_dir, backup_path=backup_path)

    assert not backup_path.exists()
    assert db_path.read_bytes() == bytes_before
    assert _sqlite_snapshot(db_path) == snapshot_before
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone() == (2,)


def test_current_v3_secret_evidence_refuses_without_mutation(tmp_path):
    data_dir = tmp_path / "current-secret"
    storage = LocalStorage(data_dir)
    evidence_id = storage.store_evidence("Initially safe", repo_id="repo-a")
    secret = "sk-proj-abcdefghijklmnopqrstuvwxyz123456"
    with storage._get_db() as conn:
        content = f"Corrupted credential {secret}"
        conn.execute(
            "UPDATE evidence SET content = ?, content_hash = ? WHERE id = ?",
            (content, LocalStorage._evidence_hash(content), evidence_id),
        )
        conn.commit()
    db_path = data_dir / "memories.db"
    bytes_before = db_path.read_bytes()
    snapshot_before = _sqlite_snapshot(db_path)

    with pytest.raises(StorageMigrationRequired, match="secret-bearing Evidence"):
        LocalStorage(data_dir)

    assert db_path.read_bytes() == bytes_before
    assert _sqlite_snapshot(db_path) == snapshot_before


def test_unversioned_legacy_store_is_refused_without_any_mutation(tmp_path):
    data_dir = tmp_path / "unversioned"
    db_path = data_dir / "memories.db"
    _create_unversioned_fixture(db_path)
    bytes_before = db_path.read_bytes()
    snapshot_before = _sqlite_snapshot(db_path)

    with pytest.raises(StorageMigrationRequired, match="[Uu]nversioned"):
        LocalStorage(data_dir)

    assert db_path.read_bytes() == bytes_before
    assert _sqlite_snapshot(db_path) == snapshot_before
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name = 'schema_migrations'"
        ).fetchone() is None


def test_v1_store_is_refused_without_any_mutation(tmp_path):
    data_dir = tmp_path / "v1"
    db_path = data_dir / "memories.db"
    _create_v2_fixture(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE schema_migrations SET version = 1")
    bytes_before = db_path.read_bytes()
    snapshot_before = _sqlite_snapshot(db_path)

    with pytest.raises(StorageMigrationRequired, match="Storage schema 1"):
        LocalStorage(data_dir)

    assert db_path.read_bytes() == bytes_before
    assert _sqlite_snapshot(db_path) == snapshot_before
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ).fetchone() == (1,)


@pytest.mark.parametrize(
    "defect",
    [
        "missing_evidence_table",
        "missing_evidence_column",
        "missing_foreign_keys",
        "invalid_hash",
        "missing_link",
    ],
)
def test_declared_v3_malformed_evidence_graph_is_refused_without_mutation(
    tmp_path, defect
):
    data_dir = tmp_path / defect
    db_path = data_dir / "memories.db"
    _create_malformed_v3_fixture(db_path, defect)
    bytes_before = db_path.read_bytes()
    snapshot_before = _sqlite_snapshot(db_path)

    with pytest.raises(StorageMigrationRequired, match="schema 3"):
        LocalStorage(data_dir)

    assert db_path.read_bytes() == bytes_before
    assert _sqlite_snapshot(db_path) == snapshot_before


def test_v2_unscoped_graph_migrates_exports_and_imports_as_quarantined(tmp_path):
    source_dir = tmp_path / "legacy-source"
    source_db = source_dir / "memories.db"
    _create_v2_unscoped_graph_fixture(source_db)
    LocalStorage.migrate_schema(source_dir, backup_path=tmp_path / "legacy-backup.db")

    source_config = MemoryConfig(repo_id=None)
    source_config.storage.data_dir = source_dir
    source_config.embedding.provider = "noop"
    source = Memory(config=source_config)
    exported_file = tmp_path / "legacy-export.json"
    exported = source.export(exported_file)

    exported_memories = [
        item for items in exported["memories"].values() for item in items
    ]
    assert {item["repo_id"] for item in exported_memories} == {UNSCOPED_REPO_ID}
    assert {item["repo_id"] for item in exported["evidence"]} == {
        UNSCOPED_REPO_ID
    }
    assert {item["repo_id"] for item in exported["intents"]} == {
        UNSCOPED_REPO_ID
    }
    assert {item["id"] for item in exported["relationships"]} == {
        "legacy-support"
    }

    target_config = MemoryConfig(repo_id=None)
    target_config.storage.data_dir = tmp_path / "legacy-target"
    target_config.embedding.provider = "noop"
    target = Memory(config=target_config)
    target.import_memories(exported_file)

    imported_belief = target._storage.get_memory("legacy-belief")
    imported_evidence = target._storage.get_evidence(
        imported_belief["evidence_ids"][0]
    )
    assert imported_belief["repo_id"] == UNSCOPED_REPO_ID
    assert imported_evidence["repo_id"] == UNSCOPED_REPO_ID
    assert imported_evidence["provenance"] == "unknown"
    assert provenance_of(imported_belief) is Provenance.UNKNOWN
    with pytest.raises(ValueError, match="reserved repository scope"):
        target.recall("Legacy authentication", repo_id=UNSCOPED_REPO_ID)


def test_v3_migration_rolls_back_transform_and_version_on_interruption(
    tmp_path, monkeypatch
):
    data_dir = tmp_path / "interrupted"
    db_path = data_dir / "memories.db"
    _create_v2_fixture(db_path)

    def interrupted(conn):
        conn.execute(
            "CREATE TABLE evidence (id TEXT PRIMARY KEY, content TEXT NOT NULL)"
        )
        conn.execute("INSERT INTO evidence VALUES ('partial', 'must roll back')")
        raise RuntimeError("simulated interruption")

    monkeypatch.setattr(LocalStorage, "_migrate_v2_to_v3", staticmethod(interrupted))

    with pytest.raises(RuntimeError, match="simulated interruption"):
        LocalStorage.migrate_schema(data_dir, backup_path=tmp_path / "interrupted-backup.db")

    with sqlite3.connect(db_path) as conn:
        version = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        evidence_table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='evidence'"
        ).fetchone()
    assert version == 2
    assert evidence_table is None


def test_newer_schema_is_refused_without_downgrade(tmp_path):
    storage = LocalStorage(tmp_path)
    storage.close()
    with sqlite3.connect(tmp_path / "memories.db") as conn:
        conn.execute(
            "INSERT INTO schema_migrations(version) VALUES (?)",
            (STORAGE_SCHEMA_VERSION + 1,),
        )

    with pytest.raises(RuntimeError, match="newer than this visp-memory build"):
        LocalStorage(tmp_path)


def test_reserved_unscoped_bucket_is_quarantined_and_never_prompt_retrievable(tmp_path):
    config = MemoryConfig(repo_id=None)
    config.storage.data_dir = tmp_path / "memory"
    config.embedding.provider = "noop"
    memory = Memory(config=config)
    memory_id = memory.record(
        "Unscoped legacy capture",
        tags=[provenance_tag(Provenance.AUTHORED)],
    )

    stored = memory._storage.get_memory(memory_id)
    assert stored["repo_id"] == UNSCOPED_REPO_ID
    assert provenance_of(stored) is Provenance.UNKNOWN
    with pytest.raises(ValueError, match="reserved repository scope"):
        memory.recall("legacy capture", repo_id=UNSCOPED_REPO_ID)
    with pytest.raises(ValueError, match="reserved repository scope"):
        memory.relevant_for(task="legacy capture", repo_id=UNSCOPED_REPO_ID)
    with pytest.raises(ValueError, match="reserved repository scope"):
        memory.context(repo_id=UNSCOPED_REPO_ID)
