from llm_memory.core.ranking import rank_memory_results, relationship_score, score_memory_result


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
