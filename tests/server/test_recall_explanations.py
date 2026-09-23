import pytest

from visp_memory.server.app import app


@pytest.mark.asyncio
async def test_keyword_recall_explains_match_and_preserves_quality_metadata(client):
    app.state.storage.store_memory(
        "SQLite backup preserves database accounts",
        repo_id="repo-a",
        metadata={"quality_flags": ["possible_conflict"], "evidence_ids": ["receipt-1"]},
        auto_link=False,
    )
    response = await client.post(
        "/recall",
        headers={"X-API-KEY": "test_key"},
        json={"repo_id": "repo-a", "query": "SQLite backup", "limit": 5},
    )
    assert response.status_code == 200, response.text
    memory = response.json()[0]
    assert memory["retrieval_method"] == "keyword"
    assert "backup, sqlite" in memory["match_explanation"]
    assert memory["metadata"]["evidence_ids"] == ["receipt-1"]
