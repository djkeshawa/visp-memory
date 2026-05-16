from llm_memory.core.ranking import rank_memory_results, score_memory_result


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
