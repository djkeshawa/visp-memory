"""Regression tests for the audit-remediation hardening pass.

Covers SQLite concurrency/integrity pragmas, FK-safe deletion ordering, the
conversation-capture record signature fix, and the learn() conflict policy.
"""

import threading

from llm_memory import Memory, MemoryConfig
from llm_memory.core.storage import LocalStorage


def test_get_db_applies_concurrency_and_integrity_pragmas(tmp_path):
    storage = LocalStorage(tmp_path)
    with storage._get_db() as conn:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] >= 30000


def test_delete_memory_with_relationship_is_fk_safe(tmp_path):
    storage = LocalStorage(tmp_path)
    a = storage.store_memory("memory a", auto_link=False)
    b = storage.store_memory("memory b", auto_link=False)
    storage.add_relationship(a, b, "related_to")

    # With foreign_keys=ON this would raise if children were deleted after the
    # parent; delete_memory must remove the relationship rows first.
    assert storage.delete_memory(a) is True
    assert storage.get_memory(a) is None
    remaining = storage.get_all_relationships()
    assert all(r["source_id"] != a and r["target_id"] != a for r in remaining)


def test_concurrent_writes_do_not_lock(tmp_path):
    storage = LocalStorage(tmp_path)
    errors: list[Exception] = []

    def writer(prefix: str):
        try:
            for i in range(25):
                storage.store_memory(f"{prefix}-{i}", auto_link=False)
        except Exception as exc:  # pragma: no cover - failure path
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(p,)) for p in ("t1", "t2")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert len(storage.list_memories(limit=1000)) == 50


def _noop_memory(tmp_path) -> Memory:
    config = MemoryConfig()
    config.storage.data_dir = tmp_path
    config.embedding.provider = "noop"
    return Memory(config=config)


def test_record_bug_with_context_does_not_raise(tmp_path):
    # Guards the conversation-capture fix: bug detail must flow through
    # episodic.record via `context=` (not the unsupported `metadata=`).
    memory = _noop_memory(tmp_path)
    memory_id = memory.record(
        event="Bug: race condition",
        category="bug_found",
        context={"cause": "missing lock", "fix": "add mutex"},
    )
    stored = memory._storage.get_memory(memory_id)
    assert stored is not None
    assert stored["metadata"].get("cause") == "missing lock"


def test_learn_conflict_creates_contradicts_relationship(tmp_path):
    memory = _noop_memory(tmp_path)
    existing_id = memory.learn("Auth tokens expire after 24 hours", category="fact")

    # Force a detected conflict against the existing memory.
    memory.conflict_detector.detect_conflicts = lambda content, relevant: {
        "conflict": True,
        "reason": "contradicts the documented expiry",
        "conflicting_ids": [existing_id],
    }

    new_id = memory.learn("Auth tokens never expire", category="fact")
    assert new_id  # must not raise even though a conflict was detected

    relationships = memory._storage.get_all_relationships()
    assert any(
        r["relationship"] == "contradicts"
        and r["source_id"] == new_id
        and r["target_id"] == existing_id
        for r in relationships
    )
