import pytest

from visp_memory.server.app import app


@pytest.mark.asyncio
async def test_recall_fills_limit_after_temporal_and_access_filtering(client):
    storage = app.state.storage
    storage.store_repository({"id": "allowed", "name": "Allowed", "team_id": "alpha"})
    account = app.state.auth_store.create_account(
        username="recall-user",
        password="correct-horse-battery-staple",
        role="user",
        team_id="alpha",
    )
    _, token = app.state.auth_store.create_token(
        user_id=account["id"],
        name="recall",
        scopes=["memory:read"],
        repo_ids=["allowed"],
    )
    for index in range(9):
        storage.store_memory(
            f"database migration private {index}",
            repo_id="allowed",
            importance=1,
            metadata={"team_id": "beta"},
            auto_link=False,
        )
        storage.store_memory(
            f"database migration expired {index}",
            repo_id="allowed",
            importance=1,
            metadata={"team_id": "alpha", "valid_to": "2020-01-01T00:00:00+00:00"},
            auto_link=False,
        )
    valid_id = storage.store_memory(
        "database migration current",
        repo_id="allowed",
        importance=0.8,
        metadata={"team_id": "alpha"},
        auto_link=False,
    )
    response = await client.post(
        "/recall",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": "database migration", "repo_id": "allowed", "limit": 1, "min_score": 0},
    )
    assert response.status_code == 200
    assert [row["id"] for row in response.json()] == [valid_id]


@pytest.mark.asyncio
async def test_http_recall_refills_after_relevance_rejections(client, monkeypatch):
    weak = {"id": "weak", "repo_id": "review", "content": "database", "importance": 0.1}
    memory_id = app.state.storage.store_memory(
        "database migration", repo_id="review", importance=0.8, auto_link=False,
    )
    relevant = {**app.state.storage.get_memory(memory_id), "similarity": 0.9}
    monkeypatch.setattr(
        app.state.storage, "search_memories", lambda **kwargs: [weak, relevant][:kwargs["limit"]],
    )
    response = await client.post(
        "/recall", headers={"X-API-KEY": "test_key"},
        json={"query": "database migration", "repo_id": "review", "layers": ["episodic"],
              "limit": 1},
    )
    assert response.status_code == 200
    assert [row["id"] for row in response.json()] == [memory_id]
