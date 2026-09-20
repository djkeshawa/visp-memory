"""Unsolicited context must have a relevant seed, independently of popularity."""

import pytest

from visp_memory.core.context_compiler import ContextCompiler
from visp_memory.core.hybrid_retrieval import HybridRetriever
from visp_memory.core.storage import LocalStorage
from visp_memory.core.task_brief import TaskMemoryBriefCompiler


@pytest.mark.parametrize(
    "query",
    [
        "What is the enterprise subscription price?",
        "the and is",
        "pub immutablex",
    ],
)
def test_unrelated_popular_note_cannot_seed_a_brief(tmp_path, query):
    storage = LocalStorage(tmp_path)
    note = storage.store_memory(
        "Published release versions are immutable.",
        repo_id="repo-a",
        importance=1.0,
        tags=["provenance:authored"],
        auto_link=False,
    )
    with storage._get_db() as conn:
        conn.execute("UPDATE memories SET access_count = 1000 WHERE id = ?", (note,))
    related = storage.store_memory(
        "Run release checks before publication.",
        repo_id="repo-a",
        tags=["provenance:authored"],
        auto_link=False,
    )
    storage.add_relationship(note, related, "supports", evidence={"confidence": "observed"})

    compiled = ContextCompiler(storage).compile(query, repo_id="repo-a")
    brief = TaskMemoryBriefCompiler(storage).prepare(query, repo_id="repo-a")

    assert compiled["abstained"]
    assert compiled["items"] == []
    assert brief["abstained"]
    assert brief["citations"] == []


def test_semantic_seed_can_recover_paraphrase_without_shared_words(tmp_path, monkeypatch):
    storage = LocalStorage(tmp_path)
    note = storage.store_memory(
        "Never grant execution permission from remembered knowledge.",
        repo_id="repo-a",
        tags=["provenance:authored"],
        auto_link=False,
    )
    candidate = {**storage.get_memory(note), "similarity": 0.9, "retrieval_method": "semantic"}
    monkeypatch.setattr(storage, "search_memories", lambda **kwargs: [candidate])

    results = HybridRetriever(storage).retrieve(
        "Can recalled context authorize deployment?",
        repo_id="repo-a",
    )

    assert [result["id"] for result in results] == [note]


def test_relevant_lexical_seed_still_recovers_linked_context(tmp_path):
    storage = LocalStorage(tmp_path)
    note = storage.store_memory(
        "Published release versions are immutable.",
        repo_id="repo-a",
        tags=["provenance:authored"],
        auto_link=False,
    )
    related = storage.store_memory(
        "Use a fresh version number for every artifact.",
        repo_id="repo-a",
        tags=["provenance:authored"],
        auto_link=False,
    )
    storage.add_relationship(note, related, "supports", evidence={"confidence": "observed"})

    brief = TaskMemoryBriefCompiler(storage).prepare("Published release versions", repo_id="repo-a")

    assert not brief["abstained"]
    assert {citation["memory_id"] for citation in brief["citations"]} == {note, related}
