"""Hybrid reranking combines evidence without relaxing recall eligibility."""

import pytest


def test_rare_query_term_breaks_dense_generic_tie():
    from visp_memory.core.lexical_ranking import rerank_lexical

    rows = [
        {"id": "generic", "content": "festival film event", "relevance_score": 0.85},
        {
            "id": "specific",
            "content": "Seattle International Film Festival",
            "relevance_score": 0.8,
        },
        {"id": "noise", "content": "festival tickets event", "relevance_score": 0.75},
    ]
    result = rerank_lexical(rows, "Seattle International Film Festival")
    assert result[0]["id"] == "specific"
    assert result[0]["relevance_score"] == 0.8
    assert "hybrid_rank_score" not in rows[1]


def test_no_lexical_signal_preserves_existing_order():
    from visp_memory.core.lexical_ranking import rerank_lexical

    rows = [{"id": "b", "content": "alpha"}, {"id": "a", "content": "beta"}]
    assert [r["id"] for r in rerank_lexical(rows, "unseen")] == ["b", "a"]


def test_empty_candidates_are_safe():
    from visp_memory.core.lexical_ranking import rerank_lexical

    assert rerank_lexical([], "query") == []


def test_hybrid_can_recover_a_candidate_below_the_requested_result_limit(tmp_path, monkeypatch):
    from visp_memory import Memory, MemoryConfig
    from visp_memory.config import EmbeddingConfig, StorageConfig

    memory = Memory(
        config=MemoryConfig(
            repo_id="repo",
            embedding=EmbeddingConfig(provider="none"),
            storage=StorageConfig(data_dir=tmp_path),
        )
    )
    rows = [
        {"id": "generic", "content": "festival film event", "similarity": 0.9},
        {"id": "specific", "content": "Seattle International Film Festival", "similarity": 0.8},
        {"id": "noise", "content": "festival tickets event", "similarity": 0.7},
    ]
    rows = [
        {
            **row,
            "repo_id": "repo",
            "layer": "episodic",
            "status": "active",
            "retrieval_method": "semantic",
            "importance": 0.5,
        }
        for row in rows
    ]
    monkeypatch.setattr(memory._storage, "search_memories", lambda **kw: rows[: kw["limit"]])
    query = "Seattle International Film Festival"
    assert memory.recall(query, limit=1)[0]["id"] == "generic"
    result = memory.recall(query, limit=1, ranking_strategy="hybrid")
    assert [row["id"] for row in result] == ["specific"]
    memory.close()


def test_hybrid_recall_respects_scope_status_and_score_floor(tmp_path, monkeypatch):
    from visp_memory import Memory, MemoryConfig
    from visp_memory.config import EmbeddingConfig, StorageConfig

    memory = Memory(
        config=MemoryConfig(
            repo_id="allowed",
            embedding=EmbeddingConfig(provider="none"),
            storage=StorageConfig(data_dir=tmp_path),
        )
    )
    for repo, status, text in [
        ("allowed", "active", "Seattle International Film Festival"),
        ("foreign", "active", "Seattle International Film Festival Seattle"),
        ("allowed", "deleted", "Seattle International Film Festival Seattle"),
        ("allowed", "active", "unrelated gardening"),
    ]:
        memory._storage.store_memory(text, repo_id=repo, status=status, auto_link=False)
    result = memory.recall(
        "Seattle International Film Festival", limit=5, ranking_strategy="hybrid", min_score=0.56
    )
    assert len(result) == 1
    assert result[0]["repo_id"] == "allowed" and result[0]["status"] == "active"
    assert result[0]["relevance_score"] >= 0.56
    assert memory.recall("anything", ranking_strategy="hybrid", limit=0) == []
    with pytest.raises(ValueError, match="ranking_strategy"):
        memory.recall("query", ranking_strategy="unknown")
    memory.close()
