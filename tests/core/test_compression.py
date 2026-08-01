"""Regression tests for MemoryCompressor date handling.

Focused on decay_old_memories tolerating rows whose ``accessed_at`` is present
but null. ``dict.get("accessed_at", fallback)`` only uses the fallback when the
key is absent, so an explicit ``accessed_at=None`` used to flow straight into
``_parse_datetime`` and crash on ``None.replace(...)``.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import pytest

from visp_memory.core.compression import MemoryCompressor
from visp_memory.core.trust import Provenance, provenance_of


class _FakeStorage:
    """Minimal storage stub exposing only what decay_old_memories touches."""

    def __init__(self, rows: List[Dict[str, Any]]):
        self._rows = rows
        self.updates: List[Dict[str, Any]] = []
        self.stores: List[Dict[str, Any]] = []
        self.relationships: List[Dict[str, Any]] = []

    def list_memories(self, layer: str = None, limit: int = 50, **kwargs):
        return [row for row in self._rows if row.get("layer") == layer]

    def update_memory(self, memory_id: str, **kwargs) -> bool:
        self.updates.append({"id": memory_id, **kwargs})
        return True

    def store_memory(self, content: str, **kwargs) -> str:
        self.stores.append({"content": content, **kwargs})
        return f"semantic-{len(self.stores)}"

    def add_relationship(self, **kwargs) -> str:
        self.relationships.append(kwargs)
        return f"relationship-{len(self.relationships)}"


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


def _episode(
    memory_id: str,
    *,
    repo_id: Any = "repo-a",
    environment: Any = None,
    task_type: Any = None,
) -> Dict[str, Any]:
    metadata = {}
    if environment is not None:
        metadata["environment"] = environment
    if task_type is not None:
        metadata["task_type"] = task_type
    return {
        "id": memory_id,
        "content": f"Deployment incident {memory_id}",
        "repo_id": repo_id,
        "category": "incident",
        "importance": 0.7,
        "metadata": metadata,
        "tags": [],
    }


def test_compression_preserves_identical_normalized_source_scope():
    storage = _FakeStorage([])
    episodes = [
        _episode(
            "one",
            environment=["PROD", "staging"],
            task_type="Deploy",
        ),
        _episode(
            "two",
            environment=["staging", "prod", "PROD"],
            task_type=["deploy"],
        ),
    ]

    semantic_id = MemoryCompressor(storage).compress_episodes_to_semantic(episodes)

    assert semantic_id == "semantic-1"
    assert len(storage.stores) == 1
    stored = storage.stores[0]
    assert stored["repo_id"] == "repo-a"
    assert stored["metadata"]["environment"] == ["prod", "staging"]
    assert stored["metadata"]["task_type"] == ["deploy"]
    assert provenance_of(stored) is Provenance.ASSISTED
    assert len(storage.updates) == 2


def test_compression_keeps_all_unscoped_dimensions_unscoped():
    storage = _FakeStorage([])

    semantic_id = MemoryCompressor(storage).compress_episodes_to_semantic(
        [_episode("one"), _episode("two")]
    )

    assert semantic_id == "semantic-1"
    metadata = storage.stores[0]["metadata"]
    assert "environment" not in metadata
    assert "task_type" not in metadata


@pytest.mark.parametrize(
    "episodes",
    [
        [_episode("one", repo_id="repo-a"), _episode("two", repo_id="repo-b")],
        [_episode("one", repo_id="repo-a"), _episode("two", repo_id=None)],
        [_episode("one", environment="prod"), _episode("two")],
        [_episode("one", environment="prod"), _episode("two", environment="dev")],
        [_episode("one", task_type="deploy"), _episode("two", task_type="review")],
        [_episode("one", environment={"prod": True}), _episode("two", environment="prod")],
    ],
)
def test_compression_refuses_ambiguous_or_malformed_source_scope(episodes):
    storage = _FakeStorage([])

    semantic_id = MemoryCompressor(storage).compress_episodes_to_semantic(episodes)

    assert semantic_id is None
    assert storage.stores == []
    assert storage.updates == []


def test_principle_compression_preserves_identical_source_scope():
    storage = _FakeStorage([])
    memories = [
        _episode(
            str(index),
            environment=["PROD", "staging"],
            task_type="Deploy",
        )
        for index in range(3)
    ]

    principle_id = MemoryCompressor(storage).compress_semantic_to_principle(memories)

    assert principle_id == "semantic-1"
    stored = storage.stores[0]
    assert stored["repo_id"] == "repo-a"
    assert stored["metadata"]["environment"] == ["prod", "staging"]
    assert stored["metadata"]["task_type"] == ["deploy"]
    assert provenance_of(stored) is Provenance.ASSISTED
    assert len(storage.relationships) == 3


@pytest.mark.parametrize(
    "memories",
    [
        [_episode("one"), _episode("two", repo_id="repo-b"), _episode("three")],
        [
            _episode("one", environment="prod"),
            _episode("two"),
            _episode("three", environment="prod"),
        ],
        [
            _episode("one", task_type="deploy"),
            _episode("two", task_type="review"),
            _episode("three", task_type="deploy"),
        ],
    ],
)
def test_principle_compression_refuses_ambiguous_source_scope(memories):
    storage = _FakeStorage([])

    principle_id = MemoryCompressor(storage).compress_semantic_to_principle(memories)

    assert principle_id is None
    assert storage.stores == []
    assert storage.relationships == []


def test_auto_compress_groups_level_one_by_repository_and_scope():
    rows = []
    for repo_id, environment in (("repo-a", "prod"), ("repo-b", "dev")):
        for index in range(3):
            row = _episode(
                f"{repo_id}-{index}",
                repo_id=repo_id,
                environment=environment,
                task_type="deploy",
            )
            row.update(layer="episodic", created_at=_iso_days_ago(30))
            rows.append(row)
    storage = _FakeStorage(rows)

    created = MemoryCompressor(storage).auto_compress(
        min_episodes=3, category_threshold=3, age_days=7
    )

    assert created == ["semantic-1", "semantic-2"]
    assert {stored["repo_id"] for stored in storage.stores} == {"repo-a", "repo-b"}
    assert {tuple(stored["metadata"]["environment"]) for stored in storage.stores} == {
        ("prod",),
        ("dev",),
    }


def test_auto_compress_groups_level_two_by_repository_and_scope():
    rows = []
    for repo_id, environment in (("repo-a", "prod"), ("repo-b", "dev")):
        for index in range(5):
            row = _episode(
                f"{repo_id}-{index}",
                repo_id=repo_id,
                environment=environment,
                task_type="deploy",
            )
            row.update(layer="semantic")
            rows.append(row)
    storage = _FakeStorage(rows)

    created = MemoryCompressor(storage).auto_compress()

    assert created == ["semantic-1", "semantic-2"]
    assert {stored["repo_id"] for stored in storage.stores} == {"repo-a", "repo-b"}
    assert all(stored["metadata"]["level"] == 2 for stored in storage.stores)


def test_auto_compress_does_not_combine_scoped_and_unscoped_records():
    rows = []
    for index, environment in enumerate(("prod", "prod", None, None)):
        row = _episode(f"mixed-{index}", environment=environment)
        row.update(layer="episodic", created_at=_iso_days_ago(30))
        rows.append(row)
    storage = _FakeStorage(rows)

    created = MemoryCompressor(storage).auto_compress(
        min_episodes=3, category_threshold=3, age_days=7
    )

    assert created == []
    assert storage.stores == []
