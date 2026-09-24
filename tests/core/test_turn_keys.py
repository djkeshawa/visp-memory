"""Conversation turns stay retrievable when their memory is about something else."""

import re

import pytest

from visp_memory.core.turn_keys import conversation_keys, key_passages

CONVERSATION = (
    "Session date: 2023/05/20 (Sat) 10:00\n"
    "user: Can you help me price my handmade soap and candles for the market?\n"
    "assistant: Sure. Start from materials, time and local competitors.\n"
    "user: ok\n"
    "user: Before this I worked as a marketing specialist at a small startup.\n"
    "assistant: That experience will help with pricing and branding.\n"
)
# A stand-in for a semantic model: words with related meaning share a dimension,
# so "occupation" matches "specialist" without sharing a word.
MEANINGS = (
    {"occupation", "job", "career", "specialist", "worked", "startup"},
    {"soap", "candles", "market", "price", "pricing", "handmade"},
    {"cat", "vet", "clinic", "pet"},
    {"weather", "hiking", "rain"},
    {"recipe", "cook", "dinner"},
)


def embed(text: str) -> list[float]:
    words = re.findall(r"[a-z]+", text.casefold())
    # A small bias keeps every vector non-zero for cosine distance.
    return [sum(word in group for word in words) + 0.05 for group in MEANINGS]


def test_keys_are_substantive_user_turns_only():
    spans = conversation_keys(CONVERSATION)
    texts = [CONVERSATION[a:b].strip() for a, b in spans]
    assert texts == [
        "user: Can you help me price my handmade soap and candles for the market?",
        "user: Before this I worked as a marketing specialist at a small startup.",
    ]


def test_passages_compete_at_the_rank_of_existing_candidates():
    memory = {"id": "m1", "content": CONVERSATION}
    a, b = conversation_keys(CONVERSATION)[1]
    hits = [
        {"memory": memory, "span": (a, b), "similarity": 0.9},
        {"memory": memory, "span": (a, b), "similarity": 0.8},  # duplicate span
    ]
    existing = [{"relevance_score": 0.7}, {"relevance_score": 0.95}]
    [passage] = key_passages(hits, existing)
    assert passage["relevance_score"] == 0.95
    assert passage["retrieval_channels"] == ["direct", "turn_key"]
    assert "marketing specialist" in passage["content"]
    # The cited span covers the matching turn (plus its short preceding turn).
    assert any(span["start"] <= a and b <= span["end"] for span in passage["passage_spans"])


def _storage(tmp_path, *, turn_keys=True):
    pytest.importorskip("chromadb")
    from visp_memory.core.storage import LocalStorage

    return LocalStorage(tmp_path, embedding_fn=embed, turn_keys=turn_keys)


def _store(storage, content=CONVERSATION, repo_id="repo-a", **kwargs):
    return storage.store_memory(content, layer="episodic", repo_id=repo_id, auto_link=False,
                                **kwargs)


def test_a_fact_stated_in_passing_is_found_by_its_turn(tmp_path):
    storage = _storage(tmp_path)
    memory_id = _store(storage)
    _store(storage, "Session date: 2023/05/21\nuser: What is a good weather app for hiking?\n")

    [hit, *_] = storage.search_turn_keys("previous occupation marketing specialist",
                                         repo_id="repo-a", limit=3)
    assert hit["memory"]["id"] == memory_id
    start, end = hit["span"]
    assert "marketing specialist" in hit["memory"]["content"][start:end]
    storage.close()


def test_keys_respect_scope_status_and_lifecycle(tmp_path):
    storage = _storage(tmp_path)
    memory_id = _store(storage)
    other = _store(storage, repo_id="repo-b")
    query = "marketing specialist startup"

    assert {h["memory"]["id"] for h in storage.search_turn_keys(query, repo_id="repo-b")} == {
        other}

    storage.update_memory(memory_id, status="archived")
    assert storage.search_turn_keys(query, repo_id="repo-a") == []
    storage.update_memory(memory_id, status="active")

    # Rewritten content is re-keyed: the old turn no longer matches.
    storage.update_memory(memory_id, content="Session date: 2023/05/20\nuser: A recipe question.\n")
    assert all("marketing" not in h["memory"]["content"]
               for h in storage.search_turn_keys(query, repo_id="repo-a"))

    assert storage.delete_memory(memory_id)
    assert storage._turn_keys.collection().get(where={"parent_id": memory_id})["ids"] == []
    storage.close()


def test_rebuild_restores_missing_keys(tmp_path):
    from visp_memory.core.indexing import ReindexScope

    storage = _storage(tmp_path)
    memory_id = _store(storage)
    storage._turn_keys.remove(memory_id)
    assert storage.search_turn_keys("marketing specialist", repo_id="repo-a") == []

    result = storage.rebuild_embedding_index(scope=ReindexScope(repo_id="repo-a"), dry_run=False)
    assert result.status == "completed"
    assert storage.search_turn_keys("marketing specialist", repo_id="repo-a")
    storage.close()


def test_disabled_or_keyword_only_stores_have_no_keys(tmp_path):
    storage = _storage(tmp_path / "off", turn_keys=False)
    _store(storage)
    assert storage.search_turn_keys("marketing specialist", repo_id="repo-a") == []
    storage.close()


# The fact is stated once, deep in a conversation whose other turns share the
# question's words ("previous"), so word-matched selection prefers them.
LONG_CONVERSATION = (
    "Session date: 2023/05/20 (Sat) 10:00\n"
    + "".join(
        f"user: My previous cat visit {n} went to the previous vet clinic downtown again.\n"
        f"assistant: {'Regular vet visits help keep a pet healthy. ' * 6}\n"
        for n in range(6)
    )
    + "user: Before all this I worked as a marketing specialist at a small startup.\n"
    + "assistant: That background will help you run the shop.\n"
)


def _brief(storage, budget=2000):
    from visp_memory.core.task_brief import TaskMemoryBriefCompiler

    return TaskMemoryBriefCompiler(storage).prepare(
        "What was my previous occupation?", repo_id="repo-a", token_budget=budget,
        context_selection="coverage",
    )["context"]


def test_turn_keys_bring_a_buried_fact_into_the_brief(tmp_path):
    fact = "marketing specialist at a small startup"
    without = _storage(tmp_path / "without", turn_keys=False)
    _store(without, LONG_CONVERSATION, tags=["provenance:authored"])
    assert fact not in _brief(without)
    without.close()

    storage = _storage(tmp_path / "with")
    _store(storage, LONG_CONVERSATION, tags=["provenance:authored"])
    assert fact in _brief(storage)
    storage.close()


def test_turn_keys_do_not_bypass_the_trust_policy(tmp_path):
    storage = _storage(tmp_path)
    # The only copy of the fact is untrusted external content.
    _store(storage, LONG_CONVERSATION, tags=["provenance:external"])
    assert "marketing specialist" not in _brief(storage)
    storage.close()


def test_default_selection_ignores_turn_keys(tmp_path):
    from visp_memory.core.task_brief import TaskMemoryBriefCompiler

    storage = _storage(tmp_path)
    storage.search_turn_keys = lambda *a, **k: pytest.fail("default selection used turn keys")
    TaskMemoryBriefCompiler(storage).prepare("What was my previous occupation?",
                                             repo_id="repo-a", token_budget=2000)
    storage.close()
