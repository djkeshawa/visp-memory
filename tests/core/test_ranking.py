from llm_memory.core.ranking import (
    ACTIVATION_RANKING_LIMIT,
    CONTEXT_RANKING_LIMIT,
    UTILITY_RANKING_LIMIT,
    activation_rank_adjustment,
    context_rank_adjustment,
    effective_halflife_days,
    graph_edge_score,
    graph_node_relevance,
    projected_importance,
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


def test_activation_adjustment_is_bounded_and_non_negative():
    assert activation_rank_adjustment(0) == 0.0
    assert activation_rank_adjustment(None) == 0.0
    assert activation_rank_adjustment(-5) == 0.0
    assert activation_rank_adjustment(1) > 0.0
    # Saturates at the cap for very frequently retrieved memories.
    assert activation_rank_adjustment(10_000) == ACTIVATION_RANKING_LIMIT
    # Monotonic in access count up to saturation.
    assert activation_rank_adjustment(2) > activation_rank_adjustment(1)


def test_frequently_used_memory_outranks_equal_peer():
    ranked = rank_memory_results(
        [
            {
                "id": "cold",
                "content": "authentication token refresh",
                "similarity": 0.4,
                "importance": 0.5,
                "access_count": 0,
            },
            {
                "id": "reinforced",
                "content": "authentication token refresh",
                "similarity": 0.4,
                "importance": 0.5,
                "access_count": 12,
            },
        ],
        query="authentication token",
    )

    assert [item["id"] for item in ranked] == ["reinforced", "cold"]


def test_total_adjustment_cap_is_load_bearing(monkeypatch):
    import llm_memory.core.ranking as ranking_module

    maxed_factors = {
        "session": {"score": 1.0},
        "task": {"score": 1.0},
        "file": {"score": 1.0},
        "repo": {"score": 1.0},
        "dependency": {"score": 1.0},
        "constraint": {"score": 1.0},
        "active_intent": {"score": 1.0},
    }
    # Noise: base 0.175, with all secondary signals maxed (uncapped boost 0.26).
    # Match: a strong vector hit with NO lexical overlap, base ~0.41 — deliberately
    # between the capped (0.325) and uncapped (0.435) noise scores, so the cap decides.
    memories = [
        {
            "id": "noise-with-everything",
            "content": "database migration complete",
            "similarity": 0.2,
            "importance": 0.5,
            "utility_score": 1.0,
            "access_count": 9999,
            "ranking_factors": maxed_factors,
        },
        {
            "id": "vector-match",
            "content": "vector embedding pipeline",  # no overlap with the query terms
            "similarity": 0.7,
            "importance": 0.4,
        },
    ]
    query = "authentication token"

    # With the cap in force, the strong direct match wins.
    ranked = rank_memory_results([dict(m) for m in memories], query=query)
    assert ranked[0]["id"] == "vector-match"

    # Remove the cap: the stacked weak signals now flip the order — proving the cap binds.
    monkeypatch.setattr(ranking_module, "TOTAL_ADJUSTMENT_LIMIT", 999.0)
    ranked_uncapped = rank_memory_results([dict(m) for m in memories], query=query)
    assert ranked_uncapped[0]["id"] == "noise-with-everything"


def test_activation_applies_on_no_query_importance_branch():
    ranked = rank_memory_results(
        [
            {"id": "cold", "content": "x", "importance": 0.5, "access_count": 0},
            {"id": "warm", "content": "x", "importance": 0.5, "access_count": 12},
        ],
        query=None,
    )
    assert [item["id"] for item in ranked] == ["warm", "cold"]


def test_projected_importance_handles_negative_and_nonnumeric_inputs():
    assert projected_importance(-0.5, age_days=1, halflife_days=30) >= 0.0
    assert effective_halflife_days(30, "not-a-number") == 30
    assert effective_halflife_days(30, -3) == 30  # negative use count treated as zero


def test_activation_cannot_override_direct_relevance():
    ranked = rank_memory_results(
        [
            {
                "id": "popular-noise",
                "content": "database migration complete",
                "similarity": 0.2,
                "importance": 0.5,
                "access_count": 9999,
            },
            {
                "id": "direct-match",
                "content": "authentication token refresh",
                "similarity": 0.4,
                "importance": 0.5,
                "access_count": 0,
            },
        ],
        query="authentication",
    )

    assert [item["id"] for item in ranked] == ["direct-match", "popular-noise"]


def test_effective_halflife_grows_with_use_and_floors_at_base():
    base = effective_halflife_days(30, 0)
    used = effective_halflife_days(30, 10)
    assert base == 30  # never-used memory keeps the base half-life (backward compatible)
    assert used > base  # repeated use stretches the half-life
    # Bad inputs degrade gracefully to the base.
    assert effective_halflife_days(30, None) == 30
    assert effective_halflife_days(None, 5) >= 1.0


def test_projected_importance_used_memory_decays_slower():
    fresh_unused = projected_importance(0.8, age_days=60, halflife_days=30, access_count=0)
    fresh_used = projected_importance(0.8, age_days=60, halflife_days=30, access_count=15)
    assert fresh_used > fresh_unused  # spaced repetition flattens the curve
    assert fresh_unused < 0.8  # still decays


def test_projected_importance_respects_floor_and_zero_age():
    assert projected_importance(0.05, age_days=10_000, halflife_days=30, min_importance=0.1) == 0.1
    # No elapsed time means no decay.
    assert projected_importance(0.7, age_days=0, halflife_days=30) == 0.7


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
