"""Regression tests for Deduplicator.merge_memories.

merge_memories used to (1) overwrite the primary memory's metadata wholesale —
wiping every pre-existing key — and (2) silently drop the merged provenance,
because the ``source_ids`` it computed were passed to an ``update_memory`` call
that has no ``source_ids`` parameter. These tests lock in that the primary's
existing metadata survives and that the merged source ids are retained.
"""

from typing import Any, Dict, List, Optional

import pytest

from llm_memory.core.storage import CHROMADB_AVAILABLE, LocalStorage
from llm_memory.quality.dedup import Deduplicator


class _FakeStorage:
    """Minimal storage stub exposing only what merge_memories touches.

    ``update_memory`` mirrors LocalStorage semantics: the metadata column is
    replaced (not deep-merged), so the test exercises the same wholesale-replace
    behaviour that made the original bug a data-loss issue.
    """

    def __init__(self, memories: Dict[str, Dict[str, Any]]):
        self._memories = memories
        self.deleted: List[str] = []

    def get_memory(self, memory_id: str) -> Optional[Dict[str, Any]]:
        return self._memories.get(memory_id)

    def update_memory(self, memory_id: str, **kwargs) -> bool:
        self._memories[memory_id].update(kwargs)
        return True

    def delete_memory(self, memory_id: str) -> bool:
        self.deleted.append(memory_id)
        self._memories.pop(memory_id, None)
        return True


def test_merge_preserves_primary_metadata_and_records_provenance():
    storage = _FakeStorage(
        {
            "primary": {
                "id": "primary",
                "content": "Primary fact",
                "metadata": {"applies_to": ["auth"], "established_at": "2026-01-01"},
                "source_ids": ["seed-1"],
            },
            "dup-1": {"id": "dup-1", "content": "Dup", "source_ids": ["seed-2"]},
            "dup-2": {"id": "dup-2", "content": "Dup"},
        }
    )

    result = Deduplicator(storage).merge_memories(["primary", "dup-1", "dup-2"])

    assert result == "primary"
    # The duplicates are deleted.
    assert storage.deleted == ["dup-1", "dup-2"]

    metadata = storage._memories["primary"]["metadata"]
    # Pre-existing metadata keys survive the merge (the original bug wiped them).
    assert metadata["applies_to"] == ["auth"]
    assert metadata["established_at"] == "2026-01-01"
    assert metadata["merged_count"] == 2
    # Provenance from the duplicates (and their own sources) is retained, in order,
    # de-duplicated, and excludes the primary itself.
    assert metadata["merged_source_ids"] == ["seed-1", "dup-1", "seed-2", "dup-2"]


def test_merge_unions_tags_from_duplicates_into_primary():
    """Tags on the merged-away duplicates must be unioned into the primary before the
    duplicates are hard-deleted, otherwise those tags are lost forever."""
    storage = _FakeStorage(
        {
            "primary": {
                "id": "primary",
                "content": "Primary fact",
                "tags": ["auth", "backend"],
            },
            "dup-1": {"id": "dup-1", "content": "Dup", "tags": ["backend", "security"]},
            "dup-2": {"id": "dup-2", "content": "Dup", "tags": ["cache"]},
            "dup-3": {"id": "dup-3", "content": "Dup"},  # no tags key at all
        }
    )

    result = Deduplicator(storage).merge_memories(["primary", "dup-1", "dup-2", "dup-3"])

    assert result == "primary"
    # Union preserves order (primary first, then newly seen tags) and de-duplicates.
    assert storage._memories["primary"]["tags"] == [
        "auth",
        "backend",
        "security",
        "cache",
    ]


def test_merge_unions_tags_persisted_via_local_storage(tmp_path):
    """End-to-end: with a real LocalStorage backend, the primary's persisted tags are
    the union of all merged memories' tags after merge_memories."""
    if not CHROMADB_AVAILABLE:
        pytest.skip("ChromaDB not installed")

    storage = LocalStorage(tmp_path)

    primary_id = storage.store_memory(
        "Primary fact", layer="semantic", tags=["auth", "backend"], auto_link=False
    )
    dup_id = storage.store_memory(
        "Duplicate fact", layer="semantic", tags=["backend", "security"], auto_link=False
    )

    result = Deduplicator(storage).merge_memories([primary_id, dup_id])

    assert result == primary_id
    assert storage.get_memory(dup_id) is None

    primary = storage.get_memory(primary_id)
    assert primary["tags"] == ["auth", "backend", "security"]


def test_merge_with_empty_list_returns_none():
    assert Deduplicator(_FakeStorage({})).merge_memories([]) is None


def test_merge_single_id_is_a_noop_keeping_metadata():
    storage = _FakeStorage(
        {"only": {"id": "only", "content": "Solo", "metadata": {"keep": True}}}
    )

    result = Deduplicator(storage).merge_memories(["only"])

    assert result == "only"
    assert storage.deleted == []
    # merged_count is 0 (no others) and the original key is preserved.
    assert storage._memories["only"]["metadata"]["keep"] is True
    assert storage._memories["only"]["metadata"]["merged_count"] == 0
