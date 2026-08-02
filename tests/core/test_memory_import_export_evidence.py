import json

import pytest

from visp_memory import Memory
from visp_memory.config import MemoryConfig
from visp_memory.core.arcadedb_storage import ArcadeDbStorage
from visp_memory.core.remote_storage import RemoteStorage
from visp_memory.core.storage import (
    UNSCOPED_REPO_ID,
    EvidenceError,
    EvidenceUnsupportedError,
    GraphImportRollbackIncompleteError,
)


def _memory(tmp_path, name):
    config = MemoryConfig(project_name=name, repo_id="repo-a")
    config.storage.data_dir = tmp_path / name
    config.embedding.provider = "noop"
    return Memory(config=config)


def _unscoped_memory(tmp_path, name):
    config = MemoryConfig(project_name=name, repo_id=None)
    config.storage.data_dir = tmp_path / name
    config.embedding.provider = "noop"
    return Memory(config=config)


def test_export_import_round_trips_evidence_lineage_lifecycle_and_graph(tmp_path):
    source = _memory(tmp_path, "source")
    episode_id = source.record("Observed exact deployment output")
    belief_id = source.learn(
        "Deployments require the verified flag",
        source_episodes=[episode_id],
    )
    source._storage.update_memory(belief_id, status="archived")
    with source._storage._get_db() as conn:
        conn.execute(
            "UPDATE memories SET compressed_at = ?, last_quality_checked_at = ? WHERE id = ?",
            (
                "2026-07-01T00:00:00+00:00",
                "2026-07-02T00:00:00+00:00",
                belief_id,
            ),
        )
        conn.commit()
    relationship_id = source._storage.add_relationship(
        episode_id, belief_id, "supports", strength=0.9
    )
    source.goal("Keep deployment evidence current", priority=2)

    export_file = tmp_path / "memory-graph.json"
    exported = source.export(export_file)

    assert exported["version"] == "2.0"
    assert exported["evidence"]
    assert relationship_id in {item["id"] for item in exported["relationships"]}
    assert exported["memories"]["semantic"][0]["evidence_ids"]

    target = _memory(tmp_path, "target")
    target.import_memories(export_file)
    target._storage.get_memory(episode_id)
    target._storage.log_recall_event(episode_id, "used")
    telemetry_before = target._storage._get_memory_row(
        episode_id, track_access=False
    )
    target.import_memories(export_file)
    telemetry_after = target._storage._get_memory_row(episode_id, track_access=False)
    assert telemetry_after["access_count"] == telemetry_before["access_count"]
    assert telemetry_after["accessed_at"] == telemetry_before["accessed_at"]

    imported_belief = target._storage.get_memory(belief_id)
    assert imported_belief["status"] == "archived"
    assert imported_belief["source_ids"] == [episode_id]
    assert imported_belief["compressed_at"] == "2026-07-01T00:00:00+00:00"
    assert imported_belief["last_quality_checked_at"] == "2026-07-02T00:00:00+00:00"
    assert imported_belief["evidence_ids"] == exported["memories"]["semantic"][0][
        "evidence_ids"
    ]
    assert relationship_id in {
        item["id"] for item in target._storage.get_all_relationships("repo-a")
    }
    assert {item["id"] for item in target._storage.list_evidence("repo-a")} == {
        item["id"] for item in exported["evidence"]
    }
    assert len(target._storage.get_active_intents("repo-a", status="all")) == 1


def test_default_scope_export_round_trips_a_self_contained_quarantined_graph(tmp_path):
    source = _unscoped_memory(tmp_path, "unscoped-source")
    episode_id = source.record("Unscoped observed deployment output")
    belief_id = source.learn(
        "Unscoped deployments require a verified flag",
        source_episodes=[episode_id],
    )
    source.record("Scoped data must not leak", repo_id="repo-b")

    export_file = tmp_path / "unscoped-graph.json"
    exported = source.export(export_file)

    exported_memories = [
        item for items in exported["memories"].values() for item in items
    ]
    exported_evidence_ids = {item["id"] for item in exported["evidence"]}
    assert {item["id"] for item in exported_memories} == {episode_id, belief_id}
    assert {item["repo_id"] for item in exported_memories} == {UNSCOPED_REPO_ID}
    assert {item["repo_id"] for item in exported["evidence"]} == {
        UNSCOPED_REPO_ID
    }
    assert all(
        set(item["evidence_ids"]) <= exported_evidence_ids
        for item in exported_memories
    )
    assert exported["stats"]["total_memories"] == 2

    target = _unscoped_memory(tmp_path, "unscoped-target")
    target.import_memories(export_file)
    imported_belief = target._storage.get_memory(belief_id)
    assert set(imported_belief["evidence_ids"]) <= {
        item["id"]
        for item in target._storage.list_evidence(UNSCOPED_REPO_ID)
    }
    assert target._storage.list_memories(repo_id="repo-b", status="all") == []
    with pytest.raises(ValueError, match="reserved repository scope"):
        target.recall("verified flag", repo_id=UNSCOPED_REPO_ID)
    assert target.recall("verified flag", repo_id="repo-a") == []

    reexported = target.export()
    assert {item["id"] for item in reexported["evidence"]} == exported_evidence_ids


def test_import_prevalidates_dangling_evidence_before_any_write(tmp_path):
    target = _memory(tmp_path, "target")
    payload = {
        "version": "2.0",
        "evidence": [],
        "memories": {
            "episodic": [],
            "semantic": [
                {
                    "id": "belief-1",
                    "content": "Dangling belief",
                    "layer": "semantic",
                    "repo_id": "repo-a",
                    "evidence_ids": ["missing-evidence"],
                }
            ],
            "raw": [],
            "intent": [],
        },
        "intents": [],
        "relationships": [],
    }
    import_file = tmp_path / "dangling.json"
    import_file.write_text(json.dumps(payload))

    with pytest.raises(ValueError, match="missing-evidence"):
        target.import_memories(import_file)

    assert target._storage.list_memories(repo_id="repo-a", status="all") == []
    assert target._storage.list_evidence("repo-a") == []


def test_import_collision_rolls_back_preceding_inserts(tmp_path):
    target = _memory(tmp_path, "target")
    target._storage.store_evidence(
        "Original immutable content",
        repo_id="repo-a",
        evidence_id="ev-collision",
    )
    payload = {
        "version": "2.0",
        "evidence": [
            {"id": "ev-new", "content": "Must roll back", "repo_id": "repo-a"},
            {
                "id": "ev-collision",
                "content": "Conflicting content",
                "repo_id": "repo-a",
            },
        ],
        "memories": {},
        "intents": [],
        "relationships": [],
    }
    import_file = tmp_path / "collision.json"
    import_file.write_text(json.dumps(payload))

    with pytest.raises(Exception, match="collision"):
        target.import_memories(import_file)

    assert target._storage.get_evidence("ev-new") is None
    assert target._storage.get_evidence("ev-collision")["content"] == (
        "Original immutable content"
    )


def test_import_forced_mid_transaction_failure_rolls_back(tmp_path, monkeypatch):
    target = _memory(tmp_path, "target")
    original_insert = target._storage._insert_evidence
    calls = 0

    def fail_on_second(conn, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("forced mid-import failure")
        return original_insert(conn, **kwargs)

    monkeypatch.setattr(target._storage, "_insert_evidence", fail_on_second)
    payload = {
        "version": "2.0",
        "evidence": [
            {"id": "ev-first", "content": "First", "repo_id": "repo-a"},
            {"id": "ev-second", "content": "Second", "repo_id": "repo-a"},
        ],
        "memories": {},
        "intents": [],
        "relationships": [],
    }
    import_file = tmp_path / "forced-failure.json"
    import_file.write_text(json.dumps(payload))

    with pytest.raises(RuntimeError, match="forced mid-import failure"):
        target.import_memories(import_file)

    assert target._storage.get_evidence("ev-first") is None
    assert target._storage.get_evidence("ev-second") is None


def test_import_refuses_secret_evidence_before_sql_or_vector_work(
    tmp_path, monkeypatch
):
    target = _memory(tmp_path, "secret-import-target")
    storage = target._storage
    secret = "sk-proj-abcdefghijklmnopqrstuvwxyz123456"
    embedding_calls = []
    storage._uses_noop_embeddings = False
    storage._embedding_fn = lambda content: embedding_calls.append(content) or [1.0]
    monkeypatch.setattr(
        storage,
        "_get_db",
        lambda: pytest.fail("graph import reached SQLite before secret refusal"),
    )
    payload = {
        "version": "2.0",
        "evidence": [
            {
                "id": "ev-secret",
                "content": f"Leaked credential {secret}",
                "repo_id": "repo-a",
            }
        ],
        "memories": {
            "episodic": [
                {
                    "id": "memory-secret",
                    "content": "Safe compatibility memory",
                    "layer": "episodic",
                    "repo_id": "repo-a",
                    "evidence_ids": ["ev-secret"],
                }
            ]
        },
        "intents": [],
        "relationships": [],
    }

    with pytest.raises(EvidenceError, match="stable imported Evidence"):
        storage.import_graph(payload, default_repo_id="repo-a")

    assert embedding_calls == []


def test_export_refuses_corrupt_secret_evidence_before_file_write(tmp_path):
    source = _memory(tmp_path, "secret-export-source")
    evidence_id = source._storage.store_evidence("Initially safe", repo_id="repo-a")
    secret = "sk-proj-abcdefghijklmnopqrstuvwxyz123456"
    with source._storage._get_db() as conn:
        content = f"Corrupted credential {secret}"
        conn.execute(
            "UPDATE evidence SET content = ?, content_hash = ? WHERE id = ?",
            (content, source._storage._evidence_hash(content), evidence_id),
        )
        conn.commit()
    export_path = tmp_path / "must-not-export-secret.json"

    with pytest.raises(EvidenceError, match="secret-bearing Evidence"):
        source.export(export_path)

    assert not export_path.exists()


def test_import_vector_failure_rolls_back_graph_and_compensates(tmp_path, monkeypatch):
    target = _memory(tmp_path, "target")

    class FailingCollection:
        def __init__(self):
            self.deleted = []

        def upsert(self, **_kwargs):
            raise RuntimeError("vector import failed")

        def get(self, *, ids, include=None):
            return {"ids": []}

        def delete(self, *, ids):
            self.deleted.extend(ids)

    collection = FailingCollection()
    target._storage._uses_noop_embeddings = False
    target._storage._embedding_fn = lambda _content: [1.0, 0.0]
    monkeypatch.setattr(target._storage, "_get_collection", lambda _layer: collection)
    payload = {
        "version": "2.0",
        "evidence": [
            {"id": "ev-vector", "content": "Vector input", "repo_id": "repo-a"}
        ],
        "memories": {
            "episodic": [
                {
                    "id": "memory-vector",
                    "content": "Vector input",
                    "layer": "episodic",
                    "repo_id": "repo-a",
                    "evidence_ids": ["ev-vector"],
                }
            ]
        },
        "intents": [],
        "relationships": [],
    }
    import_file = tmp_path / "vector-failure.json"
    import_file.write_text(json.dumps(payload))

    with pytest.raises(RuntimeError, match="vector import failed"):
        target.import_memories(import_file)

    assert target._storage.get_memory("memory-vector") is None
    assert target._storage.get_evidence("ev-vector") is None
    assert collection.deleted == ["memory-vector"]


def test_import_reports_incomplete_vector_compensation_with_recovery_ids(
    tmp_path, monkeypatch
):
    target = _memory(tmp_path, "target")

    class PartiallyFailingCollection:
        def get(self, *, ids, include=None):
            return {"ids": []}

        def upsert(self, **_kwargs):
            raise RuntimeError("vector write may be partial")

        def delete(self, *, ids):
            raise RuntimeError("vector compensation unavailable")

    target._storage._uses_noop_embeddings = False
    target._storage._embedding_fn = lambda _content: [1.0, 0.0]
    monkeypatch.setattr(
        target._storage,
        "_get_collection",
        lambda _layer: PartiallyFailingCollection(),
    )
    payload = {
        "version": "2.0",
        "evidence": [
            {"id": "ev-residual", "content": "Vector input", "repo_id": "repo-a"}
        ],
        "memories": {
            "episodic": [
                {
                    "id": "memory-residual",
                    "content": "Vector input",
                    "layer": "episodic",
                    "repo_id": "repo-a",
                    "evidence_ids": ["ev-residual"],
                }
            ]
        },
        "intents": [],
        "relationships": [],
    }
    import_file = tmp_path / "vector-compensation-failure.json"
    import_file.write_text(json.dumps(payload))

    with pytest.raises(
        GraphImportRollbackIncompleteError, match="memory-residual.*manual vector cleanup"
    ):
        target.import_memories(import_file)

    assert target._storage.get_memory("memory-residual") is None
    assert target._storage.get_evidence("ev-residual") is None


def test_repeated_import_preserves_preexisting_vector_without_upsert(
    tmp_path, monkeypatch
):
    target = _memory(tmp_path, "target")
    payload = {
        "version": "2.0",
        "evidence": [
            {"id": "ev-stable", "content": "Stable input", "repo_id": "repo-a"}
        ],
        "memories": {
            "episodic": [
                {
                    "id": "memory-stable",
                    "content": "Stable input",
                    "layer": "episodic",
                    "repo_id": "repo-a",
                    "evidence_ids": ["ev-stable"],
                }
            ]
        },
        "intents": [],
        "relationships": [],
    }
    import_file = tmp_path / "stable-vector.json"
    import_file.write_text(json.dumps(payload))
    target.import_memories(import_file)

    class ExistingCollection:
        def __init__(self):
            self.upserts = []
            self.deletes = []

        def get(self, *, ids, include=None):
            return {"ids": [item for item in ids if item == "memory-stable"]}

        def upsert(self, **kwargs):
            self.upserts.append(kwargs)

        def delete(self, *, ids):
            self.deletes.extend(ids)

    collection = ExistingCollection()
    target._storage._uses_noop_embeddings = False
    target._storage._embedding_fn = lambda _content: [1.0, 0.0]
    monkeypatch.setattr(target._storage, "_get_collection", lambda _layer: collection)

    target.import_memories(import_file)

    assert collection.upserts == []
    assert collection.deletes == []


def test_import_refuses_orphan_vector_collision_before_graph_write(
    tmp_path, monkeypatch
):
    target = _memory(tmp_path, "target")

    class OrphanCollection:
        def __init__(self):
            self.upserts = []

        def get(self, *, ids, include=None):
            return {"ids": list(ids)}

        def upsert(self, **kwargs):
            self.upserts.append(kwargs)

    collection = OrphanCollection()
    target._storage._uses_noop_embeddings = False
    target._storage._embedding_fn = lambda _content: [1.0, 0.0]
    monkeypatch.setattr(target._storage, "_get_collection", lambda _layer: collection)
    payload = {
        "version": "2.0",
        "evidence": [
            {"id": "ev-orphan", "content": "Orphan input", "repo_id": "repo-a"}
        ],
        "memories": {
            "episodic": [
                {
                    "id": "memory-orphan",
                    "content": "Orphan input",
                    "layer": "episodic",
                    "repo_id": "repo-a",
                    "evidence_ids": ["ev-orphan"],
                }
            ]
        },
        "intents": [],
        "relationships": [],
    }
    import_file = tmp_path / "orphan-vector.json"
    import_file.write_text(json.dumps(payload))

    with pytest.raises(ValueError, match="pre-existing vector"):
        target.import_memories(import_file)

    assert target._storage.get_memory("memory-orphan") is None
    assert target._storage.get_evidence("ev-orphan") is None
    assert collection.upserts == []


def test_export_refuses_to_silently_truncate_evidence(tmp_path, monkeypatch):
    source = _memory(tmp_path, "source")
    monkeypatch.setattr(
        source._storage,
        "list_evidence",
        lambda **_kwargs: [{"id": f"ev-{index}"} for index in range(10001)],
    )

    with pytest.raises(ValueError, match="Refusing to truncate Evidence export"):
        source.export()


def test_remote_export_refuses_before_reading_clamped_201_row_page(tmp_path):
    source = _memory(tmp_path, "remote-source")
    remote = RemoteStorage.__new__(RemoteStorage)
    reads = []
    remote.list_evidence = lambda **_kwargs: reads.append("evidence") or [
        {"id": f"ev-{index}"} for index in range(201)
    ]
    remote.list_memories = lambda **_kwargs: reads.append("memories") or []
    remote.get_active_intents = lambda **_kwargs: reads.append("intents") or []
    remote.get_all_relationships = lambda **_kwargs: reads.append("relationships") or []
    remote.get_stats = lambda **_kwargs: reads.append("stats") or {}
    source._storage = remote
    export_path = tmp_path / "must-not-exist.json"

    with pytest.raises(EvidenceUnsupportedError, match="complete graph export"):
        source.export(export_path)

    assert reads == []
    assert not export_path.exists()


@pytest.mark.parametrize("backend_type", [RemoteStorage, ArcadeDbStorage])
def test_non_atomic_backend_refuses_v2_import_before_any_backend_write(
    tmp_path, backend_type
):
    source = _memory(tmp_path, "import-source")
    source.record("Portable graph source")
    export_path = tmp_path / "portable.json"
    source.export(export_path)

    target = _memory(tmp_path, f"target-{backend_type.__name__}")
    backend = backend_type.__new__(backend_type)
    backend.get_capabilities = backend_type.get_capabilities.__get__(backend)
    target._storage = backend

    with pytest.raises(EvidenceUnsupportedError, match="atomic graph import"):
        target.import_memories(export_path)


@pytest.mark.parametrize(
    ("kind", "attribute"),
    [("intent", "get_active_intents"), ("relationship", "get_all_relationships")],
)
def test_export_refuses_to_truncate_aggregate_graph_kinds(
    tmp_path, monkeypatch, kind, attribute
):
    source = _memory(tmp_path, f"oversized-{kind}")
    monkeypatch.setattr(
        source._storage,
        attribute,
        lambda **_kwargs: [{"id": f"{kind}-{index}"} for index in range(10001)],
    )

    with pytest.raises(ValueError, match=f"Refusing to truncate {kind}"):
        source.export(tmp_path / f"{kind}.json")


def test_unknown_explicit_export_version_refuses_before_local_write(tmp_path):
    target = _memory(tmp_path, "unknown-version")
    import_path = tmp_path / "unknown.json"
    import_path.write_text(
        json.dumps(
            {
                "version": "3.0",
                "memories": {
                    "episodic": [{"content": "Must not be written"}],
                    "semantic": [],
                },
                "intents": [],
            }
        )
    )

    with pytest.raises(ValueError, match="Unsupported memory export version"):
        target.import_memories(import_path)

    assert target._storage.list_memories(repo_id="repo-a", status="all") == []
