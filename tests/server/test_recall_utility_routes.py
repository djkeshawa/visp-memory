import hashlib

import pytest

from visp_memory.server.app import app

HEADERS = {"X-API-KEY": "test_key"}


@pytest.fixture
def shared_scope(monkeypatch):
    monkeypatch.setenv("VISP_MEMORY_SERVER_SHARED", "true")
    monkeypatch.delenv("VISP_MEMORY_REPO_ID", raising=False)


@pytest.mark.asyncio
async def test_shared_recall_utility_routes_refuse_a_missing_repository_scope(
    client, shared_scope
):
    requests = [
        client.post(
            "/recall-events",
            json={"memory_id": "memory-1", "event_type": "used"},
            headers=HEADERS,
        ),
        client.get("/recall-events/utility", headers=HEADERS),
        client.delete("/recall-events", headers=HEADERS),
        client.get("/recall-events/verify", headers=HEADERS),
    ]

    responses = [await request for request in requests]

    assert [response.status_code for response in responses] == [400, 400, 400, 400]
    assert all(response.json()["detail"] == "repo_id is required" for response in responses)


@pytest.mark.asyncio
async def test_recall_utility_routes_hide_a_memory_from_another_repository(
    client, shared_scope
):
    memory_id = app.state.storage.store_memory("Repo B memory", repo_id="repo-b")

    responses = [
        await client.post(
            "/recall-events",
            json={
                "memory_id": memory_id,
                "event_type": "used",
                "repo_id": "repo-a",
            },
            headers=HEADERS,
        ),
        await client.get(
            "/recall-events/utility",
            params={"memory_id": memory_id, "repo_id": "repo-a"},
            headers=HEADERS,
        ),
        await client.delete(
            "/recall-events",
            params={"memory_id": memory_id, "repo_id": "repo-a"},
            headers=HEADERS,
        ),
        await client.get(
            "/recall-events/verify",
            params={"memory_id": memory_id, "repo_id": "repo-a"},
            headers=HEADERS,
        ),
    ]

    assert [response.status_code for response in responses] == [404, 404, 404, 404]
    assert all(response.json()["detail"] == "Memory not found" for response in responses)


@pytest.mark.asyncio
async def test_recall_utility_routes_round_trip_with_local_storage_shapes(
    client, shared_scope
):
    memory_id = app.state.storage.store_memory("Repo A memory", repo_id="repo-a")

    created = await client.post(
        "/recall-events",
        json={
            "memory_id": memory_id,
            "event_type": "task-linked",
            "repo_id": "repo-a",
            "query": "private query",
            "task_id": "task-1",
            "outcome": "helpful",
            "metadata": {"source": "route-test", "prompt": "must be removed"},
        },
        headers=HEADERS,
    )
    assert created.status_code == 200
    event_id = created.json()["id"]

    inspected = await client.get(
        "/recall-events/utility",
        params={"memory_id": memory_id, "repo_id": "repo-a", "limit": 5},
        headers=HEADERS,
    )
    assert inspected.status_code == 200
    report = inspected.json()
    assert report["summary"] == {
        "total_events": 1,
        "by_event_type": {"task_linked": 1},
        "memories": 1,
    }
    assert report["events"][0]["id"] == event_id
    assert report["events"][0]["query_hash"] == hashlib.sha256(
        b"private query"
    ).hexdigest()
    assert report["events"][0]["metadata"] == {"source": "route-test"}

    verified = await client.get(
        "/recall-events/verify",
        params={"memory_id": memory_id, "repo_id": "repo-a"},
        headers=HEADERS,
    )
    assert verified.status_code == 200
    assert verified.json() == {
        "valid": True,
        "checked_events": 1,
        "cross_repository_events": 0,
        "violations": [],
    }

    reset = await client.delete(
        "/recall-events",
        params={"memory_id": memory_id, "repo_id": "repo-a"},
        headers=HEADERS,
    )
    assert reset.status_code == 200
    assert reset.json() == {"deleted": 1}


@pytest.mark.asyncio
async def test_reset_recall_utility_refuses_an_archived_repository(
    client, shared_scope
):
    memory_id = app.state.storage.store_memory("Archived memory", repo_id="repo-a")
    app.state.storage.store_repository({"id": "repo-a", "name": "Repo A"})
    app.state.storage.update_repository("repo-a", status="archived")

    response = await client.delete(
        "/recall-events",
        params={"memory_id": memory_id, "repo_id": "repo-a"},
        headers=HEADERS,
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Repository is archived and does not accept new writes"


@pytest.mark.asyncio
async def test_recall_utility_routes_reject_an_unknown_event_type(client, shared_scope):
    memory_id = app.state.storage.store_memory("Repo A memory", repo_id="repo-a")

    response = await client.post(
        "/recall-events",
        json={
            "memory_id": memory_id,
            "event_type": "invented",
            "repo_id": "repo-a",
        },
        headers=HEADERS,
    )

    assert response.status_code == 422
    assert "Invalid recall event type" in response.json()["detail"]
