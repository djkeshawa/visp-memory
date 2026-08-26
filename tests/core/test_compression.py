"""Regression tests for MemoryCompressor date handling.

Focused on decay_old_memories tolerating rows whose ``accessed_at`` is present
but null. ``dict.get("accessed_at", fallback)`` only uses the fallback when the
key is absent, so an explicit ``accessed_at=None`` used to flow straight into
``_parse_datetime`` and crash on ``None.replace(...)``.
"""

import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from visp_memory.core.compression import MemoryCompressor, create_llm_compressor
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
    assert compressor._parse_datetime("2024-01-01T00:00:00").tzinfo is not None


def test_compress_episodes_uses_llm_result_and_marks_sources():
    storage = _FakeStorage([])
    calls = []

    def compress(contents):
        calls.append(contents)
        return "LLM-derived deployment pattern"

    episodes = [_episode("one"), _episode("two")]
    semantic_id = MemoryCompressor(storage, llm_compress_fn=compress).compress_episodes_to_semantic(
        episodes, category="fact"
    )

    assert semantic_id == "semantic-1"
    assert calls == [[episode["content"] for episode in episodes]]
    assert storage.stores[0]["content"] == "LLM-derived deployment pattern"
    assert storage.stores[0]["source_ids"] == ["one", "two"]
    assert len(storage.updates) == 2


def test_compression_skips_writes_when_llm_returns_empty():
    storage = _FakeStorage([])
    episodes = [_episode("one"), _episode("two")]

    compressor = MemoryCompressor(storage, llm_compress_fn=lambda _contents: "")
    assert compressor.compress_episodes_to_semantic(episodes) is None
    assert storage.stores == []
    assert storage.updates == []


def test_compression_refuses_non_mapping_metadata_without_writes():
    first = _episode("one")
    second = _episode("two")
    first["metadata"] = ["not", "a", "mapping"]
    storage = _FakeStorage([])

    assert MemoryCompressor(storage).compress_episodes_to_semantic([first, second]) is None
    assert storage.stores == []


def test_heuristic_compression_covers_singleton_and_no_keyword_fallback():
    compressor = MemoryCompressor(_FakeStorage([]))

    assert compressor._heuristic_compress(["One incident"]) == "Pattern observed: One incident"
    assert compressor._heuristic_compress(["the and is", "the and is"]) == (
        "Pattern (2 instances): the and is..."
    )


def test_principle_compression_uses_llm_prompt_and_restores_callback():
    storage = _FakeStorage([])
    calls = []

    def compress(contents):
        calls.append(contents)
        return "Universal deployment principle"

    compressor = MemoryCompressor(storage, llm_compress_fn=compress)
    memories = [_episode(str(index)) for index in range(3)]

    principle_id = compressor.compress_semantic_to_principle(memories)

    assert principle_id == "semantic-1"
    assert calls[0][-1].startswith("\n\nExtract the underlying universal principle")
    assert calls[0][:-1] == [memory["content"] for memory in memories]
    assert compressor._llm_compress is compress
    assert len(storage.relationships) == 3


def test_principle_compression_restores_callback_when_llm_fails():
    storage = _FakeStorage([])

    def compress(_contents):
        raise RuntimeError("provider unavailable")

    compressor = MemoryCompressor(storage, llm_compress_fn=compress)
    memories = [_episode(str(index)) for index in range(3)]

    with pytest.raises(RuntimeError, match="provider unavailable"):
        compressor.compress_semantic_to_principle(memories)

    assert compressor._llm_compress is compress
    assert storage.stores == []


def test_auto_compress_skips_compressed_undated_unscoped_and_small_groups():
    compressed = _episode("compressed")
    compressed.update(
        layer="episodic", created_at=_iso_days_ago(30), metadata={"compressed_to": "s"}
    )
    undated = _episode("undated")
    undated.update(layer="episodic", created_at=None)
    unscoped = _episode("unscoped", repo_id=None)
    unscoped.update(layer="episodic", created_at=_iso_days_ago(30))
    semantic_unscoped = _episode("semantic-unscoped", repo_id=None)
    semantic_unscoped.update(layer="semantic", metadata={"level": 1})
    storage = _FakeStorage([compressed, undated, unscoped, semantic_unscoped])

    created = MemoryCompressor(storage).auto_compress(
        min_episodes=1, category_threshold=3, age_days=7
    )

    assert created == []
    assert storage.stores == []


def test_decay_skips_recent_and_floor_rows_and_recovers_invalid_importance():
    old = _iso_days_ago(365)
    rows = [
        {
            "id": "recent",
            "layer": "episodic",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "importance": 0.9,
            "access_count": 0,
        },
        {
            "id": "floor",
            "layer": "episodic",
            "created_at": old,
            "importance": 0.1,
            "access_count": 0,
        },
        {
            "id": "invalid-importance",
            "layer": "semantic",
            "created_at": old,
            "importance": "not-a-number",
            "access_count": 0,
        },
    ]
    storage = _FakeStorage(rows)

    assert MemoryCompressor(storage).decay_old_memories() == 1
    assert [update["id"] for update in storage.updates] == ["invalid-importance"]
    assert storage.updates[0]["importance"] < 0.5


def test_create_llm_compressors_adapt_provider_response_shapes(monkeypatch):
    openai_calls = []

    class FakeOpenAI:
        def __init__(self, api_key=None):
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=self._create)
            )
            self.api_key = api_key

        def _create(self, **kwargs):
            openai_calls.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=" openai result "))]
            )

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    openai_compress = create_llm_compressor("openai", model="test-openai", api_key="secret")
    assert openai_compress(["first", "second"]) == "openai result"
    assert openai_calls[0]["model"] == "test-openai"
    assert "first" in openai_calls[0]["messages"][0]["content"]

    anthropic_calls = []

    class FakeAnthropic:
        def __init__(self, api_key=None):
            self.api_key = api_key
            self.messages = SimpleNamespace(create=self._create)

        def _create(self, **kwargs):
            anthropic_calls.append(kwargs)
            return SimpleNamespace(content=[SimpleNamespace(text=" anthropic result ")])

    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(Anthropic=FakeAnthropic))
    anthropic_compress = create_llm_compressor(
        "anthropic", model="test-anthropic", api_key="secret"
    )
    assert anthropic_compress(["fact"]) == "anthropic result"
    assert anthropic_calls[0]["model"] == "test-anthropic"

    ollama_calls = []

    def fake_ollama_chat(**kwargs):
        ollama_calls.append(kwargs)
        return {"message": {"content": " ollama result "}}

    monkeypatch.setitem(sys.modules, "ollama", SimpleNamespace(chat=fake_ollama_chat))
    ollama_compress = create_llm_compressor("ollama", model="test-ollama")
    assert ollama_compress(["rule"]) == "ollama result"
    assert ollama_calls[0]["model"] == "test-ollama"


def test_create_llm_compressor_rejects_unknown_provider():
    with pytest.raises(ValueError, match="Unknown provider"):
        create_llm_compressor("unsupported")


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
    assert stored["category"] == "hypothesis"
    assert stored["metadata"]["legacy_category"] == "incident"
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
    assert stored["category"] == "procedure"
    assert stored["metadata"]["legacy_category"] == "principle"
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
