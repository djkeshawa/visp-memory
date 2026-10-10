"""Regression tests for hierarchical compression found in the module audit.

- MG-048: Level 2 must not promote hypotheses into a canonical principle.
- Re-running compression must not mint a duplicate principle from the same facts.
- Level 1 must page past old uncompressible episodes instead of starving newer ones.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from visp_memory.core.compression import MemoryCompressor
from visp_memory.core.injection import _is_hypothesis
from visp_memory.core.storage import LocalStorage


def _store_semantic(storage: LocalStorage, content: str, category: str) -> str:
    evidence = storage.store_evidence(
        f"source for {content}", repo_id="r", evidence_type="test-source", provenance="authored"
    )
    return storage.store_memory(
        content,
        layer="semantic",
        repo_id="r",
        category=category,
        tags=["provenance:authored"],
        evidence_ids=[evidence],
        auto_link=False,
    )


def _principles(storage: LocalStorage) -> List[Dict[str, Any]]:
    rows = storage.list_memories(layer="semantic", limit=100)
    return [row for row in rows if (row.get("metadata") or {}).get("level") == 2]


def test_level_two_never_turns_hypotheses_into_a_canonical_principle(tmp_path):
    storage = LocalStorage(tmp_path, embedding_fn=lambda _: [1.0])
    ids = [
        _store_semantic(storage, f"Maybe the flaky deploy is caused by warmup {i}", "hypothesis")
        for i in range(5)
    ]
    assert all(_is_hypothesis(storage.get_memory(memory_id)) for memory_id in ids)

    compressor = MemoryCompressor(storage)

    assert compressor.auto_compress() == []
    assert _principles(storage) == []
    # The public Level-2 entry point refuses guesses too.
    rows = [storage.get_memory(memory_id) for memory_id in ids]
    assert compressor.compress_semantic_to_principle(rows) is None
    assert _principles(storage) == []


def test_repeated_compression_does_not_duplicate_principles(tmp_path):
    storage = LocalStorage(tmp_path, embedding_fn=lambda _: [1.0])
    for i in range(5):
        _store_semantic(storage, f"Deploy rule {i}: run migrations before restart v{i}", "fact")
    compressor = MemoryCompressor(storage)

    first = compressor.auto_compress()
    assert len(first) == 1
    assert compressor.auto_compress() == []
    assert compressor.auto_compress() == []

    principles = _principles(storage)
    assert [row["id"] for row in principles] == first
    # Principles are stored as procedures; they must still be recognised as principles.
    assert principles[0]["category"] == "procedure"
    assert "principle" in principles[0]["tags"]


class _PagingStorage:
    """Storage stub that honours ``limit``/``order_by``/``after_id`` like LocalStorage."""

    def __init__(self, rows: List[Dict[str, Any]]):
        self._rows = rows
        self.stores: List[Dict[str, Any]] = []

    def list_memories(self, layer=None, limit=50, order_by="created_at DESC", after_id=None,
                      **kwargs):
        rows = [row for row in self._rows if row.get("layer") == layer]
        field, _, direction = order_by.partition(" ")
        rows.sort(key=lambda row: row[field], reverse=direction == "DESC")
        if after_id is not None:
            rows = [row for row in rows if row["id"] > after_id]
        return [dict(row) for row in rows[:limit]]

    def get_memory(self, memory_id):
        return next((dict(row) for row in self._rows if row["id"] == memory_id), None)

    peek_memory = get_memory

    def update_memory(self, memory_id, **kwargs):
        return True

    def store_memory(self, content, **kwargs):
        self.stores.append({"content": content, **kwargs})
        return f"semantic-{len(self.stores)}"

    def add_relationship(self, **kwargs):
        return "relationship"

    def get_related_memories(self, memory_id, relationship=None):
        return []


def test_level_one_reaches_episodes_beyond_the_first_page():
    base = datetime.now(timezone.utc) - timedelta(days=60)
    rows = []
    # 500 oldest episodes in pairs per category: below the threshold, never compressible.
    for i in range(500):
        rows.append({
            "id": f"z-old-{i:04d}",
            "layer": "episodic",
            "repo_id": "r",
            "content": f"old note {i}",
            "category": f"cat{i // 2}",
            "created_at": (base + timedelta(seconds=i)).isoformat(),
            "status": "active",
            "metadata": {},
        })
    # Newer (still old enough) episodes that clearly form a compressible group.
    for i in range(5):
        rows.append({
            "id": f"a-new-{i}",
            "layer": "episodic",
            "repo_id": "r",
            "content": f"flaky test in payments retry {i}",
            "category": "bug",
            "created_at": (base + timedelta(days=10, seconds=i)).isoformat(),
            "status": "active",
            "metadata": {},
        })
    storage = _PagingStorage(rows)

    created = MemoryCompressor(storage).auto_compress()

    assert created == ["semantic-1"]
    assert sorted(storage.stores[0]["source_ids"]) == [f"a-new-{i}" for i in range(5)]
