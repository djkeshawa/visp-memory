import pytest

from visp_memory.server.app import app

HEADERS = {"X-API-KEY": "test_key"}


@pytest.fixture
def shared_scope(monkeypatch):
    monkeypatch.setenv("VISP_MEMORY_SERVER_SHARED", "true")
    monkeypatch.delenv("VISP_MEMORY_REPO_ID", raising=False)


@pytest.mark.asyncio
async def test_shared_memory_inspection_routes_refuse_a_missing_repository_scope(
    client, shared_scope
):
    memory_id = app.state.storage.store_memory("Scoped memory", repo_id="repo-a")

    responses = [
        await client.get(f"/memories/{memory_id}/peek", headers=HEADERS),
        await client.post(
            "/turn-keys/search", json={"query": "scoped"}, headers=HEADERS
        ),
        await client.get("/intents/usage", headers=HEADERS),
    ]

    assert [response.status_code for response in responses] == [400, 400, 400]
    assert all(response.json()["detail"] == "repo_id is required" for response in responses)


@pytest.mark.asyncio
async def test_peek_route_is_ordered_and_does_not_increment_access_count(
    client, shared_scope
):
    memory_id = app.state.storage.store_memory("Peek without recall", repo_id="repo-a")
    before = app.state.storage.peek_memory(memory_id)

    response = await client.get(
        f"/memories/{memory_id}/peek",
        params={"repo_id": "repo-a"},
        headers=HEADERS,
    )

    assert response.status_code == 200
    assert response.json()["id"] == memory_id
    assert response.json()["access_count"] == before["access_count"]
    assert app.state.storage.peek_memory(memory_id)["access_count"] == before["access_count"]


@pytest.mark.asyncio
async def test_peek_route_hides_a_record_outside_the_requested_repository(
    client, shared_scope
):
    memory_id = app.state.storage.store_memory("Repo B memory", repo_id="repo-b")

    response = await client.get(
        f"/memories/{memory_id}/peek",
        params={"repo_id": "repo-a"},
        headers=HEADERS,
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Memory not found"


@pytest.mark.asyncio
async def test_turn_key_search_route_is_scoped_and_returns_the_storage_shape(
    client, shared_scope, monkeypatch
):
    hit = {
        "memory": {"id": "memory-a", "repo_id": "repo-a"},
        "span": "The matching conversation turn",
        "similarity": 0.91,
    }
    calls = []

    def search(query, *, repo_id, limit, status):
        calls.append((query, repo_id, limit, status))
        return [hit]

    monkeypatch.setattr(app.state.storage, "search_turn_keys", search)

    response = await client.post(
        "/turn-keys/search",
        json={
            "query": "matching",
            "repo_id": "repo-a",
            "limit": 3,
            "status": "active",
        },
        headers=HEADERS,
    )

    assert response.status_code == 200
    assert response.json() == [hit]
    assert calls == [("matching", "repo-a", 3, "active")]


@pytest.mark.asyncio
async def test_intent_usage_route_is_ordered_and_repository_scoped(
    client, shared_scope
):
    app.state.storage.store_memory("Repo A memory", repo_id="repo-a")
    app.state.storage.store_memory("Repo B memory", repo_id="repo-b")
    app.state.storage.set_intent("Repo A intent", repo_id="repo-a")
    app.state.storage.set_intent("Repo B intent", repo_id="repo-b")

    response = await client.get(
        "/intents/usage", params={"repo_id": "repo-a"}, headers=HEADERS
    )

    assert response.status_code == 200
    assert response.json() == {
        "exists": True,
        "memories": 1,
        "active_intents": 1,
        "total_intents": 1,
    }


@pytest.mark.asyncio
async def test_repository_registration_inspection_only_returns_the_path_scope(
    client, shared_scope
):
    app.state.storage.store_memory("Repo A memory", repo_id="repo-a")
    app.state.storage.store_memory("Repo B memory", repo_id="repo-b")

    response = await client.get(
        "/repos/repo-a/registration", headers=HEADERS
    )

    assert response.status_code == 200
    assert response.json() == {
        "exists": True,
        "project_scopes": ["repo-a"],
        "unregistered_scopes": [],
    }


@pytest.mark.asyncio
async def test_capabilities_route_serializes_the_storage_dataclass(client):
    response = await client.get("/diagnostics/capabilities", headers=HEADERS)

    assert response.status_code == 200
    assert response.json() == app.state.storage.get_capabilities().to_dict()
