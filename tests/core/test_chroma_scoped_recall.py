"""Exercise real Chroma filter validation, without a downloaded embedding model."""

import pytest

from visp_memory.core.storage import LocalStorage


def test_scoped_vector_recall_filters_before_ranking(tmp_path):
    pytest.importorskip("chromadb")
    storage = LocalStorage(tmp_path, embedding_fn=lambda text: [1.0, 0.0, 0.0])
    expected = storage.store_memory(
        "Keep verified engineering knowledge",
        repo_id="repo-a",
        category="decision",
        importance=0.8,
        auto_link=False,
    )
    storage.store_memory(
        "Foreign context",
        repo_id="repo-b",
        category="decision",
        auto_link=False,
    )
    storage.store_memory(
        "Obsolete context",
        repo_id="repo-a",
        category="decision",
        status="deleted",
        auto_link=False,
    )
    storage.store_memory(
        "Low importance context",
        repo_id="repo-a",
        category="decision",
        importance=0.1,
        auto_link=False,
    )
    storage.store_memory(
        "Different category",
        repo_id="repo-a",
        category="note",
        auto_link=False,
    )

    results = storage.search_memories(
        "unshared vocabulary",
        repo_id="repo-a",
        layer="episodic",
        category="decision",
        min_importance=0.5,
        status="active",
        limit=1,
    )

    assert [item["id"] for item in results] == [expected]
    assert results[0]["retrieval_method"] == "semantic"
    storage.close()
