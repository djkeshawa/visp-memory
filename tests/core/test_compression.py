"""Regression tests for MemoryCompressor date handling.

Focused on decay_old_memories tolerating rows whose ``accessed_at`` is present
but null. ``dict.get("accessed_at", fallback)`` only uses the fallback when the
key is absent, so an explicit ``accessed_at=None`` used to flow straight into
``_parse_datetime`` and crash on ``None.replace(...)``.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from llm_memory.core.compression import MemoryCompressor


class _FakeStorage:
    """Minimal storage stub exposing only what decay_old_memories touches."""

    def __init__(self, rows: List[Dict[str, Any]]):
        self._rows = rows
        self.updates: List[Dict[str, Any]] = []

    def list_memories(self, layer: str = None, limit: int = 50, **kwargs):
        return [row for row in self._rows if row.get("layer") == layer]

    def update_memory(self, memory_id: str, **kwargs) -> bool:
        self.updates.append({"id": memory_id, **kwargs})
        return True


def _iso_days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def test_decay_handles_explicit_none_accessed_at():
    """accessed_at present-but-None must fall back to created_at, not crash."""
    rows = [
        {
            "id": "e",
            "layer": "semantic",
            "accessed_at": None,
            "created_at": _iso_days_ago(365),
            "importance": 0.9,
            "access_count": 0,
        }
    ]
    storage = _FakeStorage(rows)

    decayed = MemoryCompressor(storage).decay_old_memories()

    # The row is a year old, so it decays toward the floor and is updated once.
    assert decayed == 1
    assert len(storage.updates) == 1
    assert storage.updates[0]["id"] == "e"
    assert storage.updates[0]["importance"] < 0.9


def test_decay_skips_rows_with_no_dateable_field():
    """A row with neither accessed_at nor created_at is skipped, not fatal."""
    rows = [
        {
            "id": "undateable",
            "layer": "semantic",
            "accessed_at": None,
            "created_at": None,
            "importance": 0.9,
            "access_count": 0,
        }
    ]
    storage = _FakeStorage(rows)

    decayed = MemoryCompressor(storage).decay_old_memories()

    assert decayed == 0
    assert storage.updates == []


def test_decay_uses_created_at_when_accessed_at_key_absent():
    """The original fallback path (key missing entirely) still works."""
    rows = [
        {
            "id": "no-accessed-key",
            "layer": "episodic",
            "created_at": _iso_days_ago(365),
            "importance": 0.9,
            "access_count": 0,
        }
    ]
    storage = _FakeStorage(rows)

    decayed = MemoryCompressor(storage).decay_old_memories()

    assert decayed == 1
    assert storage.updates[0]["id"] == "no-accessed-key"


def test_auto_compress_tolerates_present_but_none_metadata():
    """auto_compress must not crash on rows whose metadata is present-but-None.

    ``dict.get("metadata", {})`` returns ``None`` (not ``{}``) when the key exists
    with a null value, so the metadata filters used to raise
    ``AttributeError: 'NoneType' object has no attribute 'get'``.
    """
    rows = [
        {
            "id": "ep-old",
            "layer": "episodic",
            "content": "Something happened",
            "category": "note",
            "created_at": _iso_days_ago(365),
            "metadata": None,  # used to crash in the Level-1 uncompressed filter
        },
        {
            "id": "sem-fact",
            "layer": "semantic",
            "content": "A fact",
            "category": "fact",
            "metadata": None,  # used to crash in the Level-2 facts filter
        },
    ]
    storage = _FakeStorage(rows)

    # A single old episode / single fact is below the compression thresholds, so
    # nothing is consolidated, but the metadata-reading filters still run on every row.
    created = MemoryCompressor(storage).auto_compress()

    assert created == []


def test_parse_datetime_returns_none_for_falsy_or_garbage():
    compressor = MemoryCompressor(_FakeStorage([]))

    assert compressor._parse_datetime(None) is None
    assert compressor._parse_datetime("") is None
    assert compressor._parse_datetime("not-a-date") is None

    parsed = compressor._parse_datetime("2024-01-01T00:00:00Z")
    assert parsed is not None
    assert parsed.tzinfo is not None
