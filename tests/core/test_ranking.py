from llm_memory.core.ranking import (
    CONTEXT_RANKING_LIMIT,
    UTILITY_RANKING_LIMIT,
    context_rank_adjustment,
    graph_edge_score,
    graph_node_relevance,
    rank_memory_results,
    relationship_score,
    score_memory_result,
    utility_rank_adjustment,
)


def test_relevance_score_combines_similarity_text_and_importance():
    exact = {
        "id": "exact",
        "content": "authentication token refresh",
        "similarity": 0.2,
        "importance": 0.9,
    }
    unrelated = {
        "id": "unrelated",
        "content": "database migration complete",
        "similarity": 0.2,
        "importance": 0.9,
    }

    assert score_memory_result(exact, query="authentication") > score_memory_result(
        unrelated, query="authentication"
    )


def test_rank_memory_results_deduplicates_and_adds_canonical_score():
    ranked = rank_memory_results(
        [
            {"id": "a", "content": "database migration", "similarity": 0.4, "importance": 0.5},
            {"id": "a", "content": "database migration", "similarity": 0.4, "importance": 0.5},
            {"id": "b", "content": "auth token", "similarity": 0.4, "importance": 0.5},
        ],
        query="auth",
    )

    assert [item["id"] for item in ranked] == ["b", "a"]
    assert all("relevance_score" in item for item in ranked)


def test_rank_memory_results_filters_below_min_score():
    ranked = rank_memory_results(
        [
            {
                "id": "noise",
                "content": "database migration complete",
                "similarity": 0.5,
                "importance": 0.5,
            }
        ],
        query="banana bread recipe",
        min_score=0.56,
    )

    assert ranked == []


def test_utility_adjustment_is_bounded():
    assert utility_rank_adjustment(99) == UTILITY_RANKING_LIMIT
    assert utility_rank_adjustment(-99) == -UTILITY_RANKING_LIMIT


def test_utility_cannot_override_direct_relevance():
    ranked = rank_memory_results(
        [
            {
                "id": "popular-noise",
                "content": "database migration complete",
                "similarity": 0.2,
                "importance": 0.9,
                "utility_score": 1.0,
            },
            {
                "id": "direct-match",
                "content": "authentication token refresh",
                "similarity": 0.4,
                "importance": 0.3,
                "utility_score": -1.0,
            },
        ],
        query="authentication",
    )

    assert [item["id"] for item in ranked] == ["direct-match", "popular-noise"]


def test_context_adjustment_is_bounded_and_explained():
    factors = {
        "session": {"score": 1.0, "reason": "session"},
        "task": {"score": 1.0, "reason": "task"},
        "file": {"score": 1.0, "reason": "file"},
        "repo": {"score": 1.0, "reason": "repo"},
        "dependency": {"score": 1.0, "reason": "dependency"},
        "constraint": {"score": 1.0, "reason": "constraint"},
        "active_intent": {"score": 1.0, "reason": "intent"},
    }

    ranked = rank_memory_results(
        [
            {
                "id": "contextual",
                "content": "auth refresh",
                "similarity": 0.3,
                "importance": 0.5,
                "ranking_factors": factors,
            }
        ],
        query="auth",
    )

    assert context_rank_adjustment(factors) == CONTEXT_RANKING_LIMIT
    assert ranked[0]["ranking_explanation"]
    assert any("active_intent" in item for item in ranked[0]["ranking_explanation"])


def test_relationship_score_requires_more_than_embedding_noise():
    assert (
        relationship_score(
            0.55,
            "banana bread recipe",
            "Docker server uses OpenRouter embeddings",
        )
        == 0.0
    )
    assert relationship_score(
        0.75,
        "OpenRouter cloud embeddings",
        "Cloud embeddings use OpenRouter",
    ) >= 0.60


def test_graph_scores_include_evidence_and_distance():
    observed = graph_edge_score(
        0.7,
        {"confidence": "observed", "confidence_score": 0.9},
    )
    ambiguous = graph_edge_score(
        0.7,
        {"confidence": "ambiguous", "confidence_score": 0.3},
    )

    assert observed > ambiguous
    assert graph_node_relevance(0.9, 0.7, observed, distance=0) > graph_node_relevance(
        0.9, 0.7, observed, distance=3
    )
