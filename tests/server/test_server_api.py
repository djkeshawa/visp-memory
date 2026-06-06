import sqlite3

import pytest

from llm_memory.server import app as server_app
from llm_memory.server.app import app


@pytest.mark.asyncio
async def test_root_endpoint(client):
    response = await client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "online"
    assert "version" in data
    assert "stats" in data
    assert "total_memories" in data
    assert "storage_backend" in data
    assert "embedding_provider" in data


@pytest.mark.asyncio
async def test_root_endpoint_filters_stats_by_repo_id(client):
    headers = {"X-API-KEY": "test_key"}
    await client.post(
        "/memories",
        json={"content": "Repo A memory", "repo_id": "repo-a"},
        headers=headers,
    )
    await client.post(
        "/memories",
        json={"content": "Repo B memory", "repo_id": "repo-b"},
        headers=headers,
    )

    response = await client.get("/?repo_id=repo-a")

    assert response.status_code == 200
    data = response.json()
    assert data["stats"]["total_memories"] == 1
    assert data["total_memories"] == 1
    assert data["stats"]["memories_by_layer"] == {"episodic": 1}


@pytest.mark.asyncio
async def test_healthz_is_unauthenticated(client):
    response = await client.get("/healthz")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "version" in data


@pytest.mark.asyncio
async def test_readyz_reports_runtime_readiness(client, tmp_path, monkeypatch):
    monkeypatch.setattr(server_app, "STATIC_DIR", tmp_path)

    response = await client.get("/readyz")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"
    assert data["storage_ready"] is True
    assert "storage_backend" in data
    assert "embedding_provider" in data
    assert "auth_enabled" in data
    assert "dashboard_static_available" in data


@pytest.mark.asyncio
async def test_readyz_returns_503_when_dashboard_static_is_missing(client, tmp_path, monkeypatch):
    monkeypatch.setattr(server_app, "STATIC_DIR", tmp_path / "missing")

    response = await client.get("/readyz")

    assert response.status_code == 503
    data = response.json()
    assert data["status"] == "not_ready"
    assert data["storage_ready"] is True
    assert data["dashboard_static_available"] is False


@pytest.mark.asyncio
async def test_readyz_returns_503_when_storage_check_fails(client, tmp_path, monkeypatch):
    monkeypatch.setattr(server_app, "STATIC_DIR", tmp_path)

    class FailingStorage:
        def get_stats(self):
            raise RuntimeError("database unavailable")

    previous_storage = app.state.storage
    app.state.storage = FailingStorage()
    try:
        response = await client.get("/readyz")
    finally:
        app.state.storage = previous_storage

    assert response.status_code == 503
    data = response.json()
    assert data["status"] == "not_ready"
    assert data["storage_ready"] is False
    assert data["storage_error"] == "RuntimeError"
    assert "database unavailable" not in str(data)


@pytest.mark.asyncio
async def test_project_scopes_include_registered_and_memory_repo_ids(client):
    headers = {"X-API-KEY": "test_key"}
    await client.post(
        "/repos",
        json={"id": "registered-repo", "name": "Registered Repo"},
        headers=headers,
    )
    await client.post(
        "/memories",
        json={"content": "Repo scoped memory", "repo_id": "memory-repo"},
        headers=headers,
    )

    response = await client.get("/repos/scopes", headers=headers)

    assert response.status_code == 200
    scopes = {scope["id"]: scope for scope in response.json()}
    assert scopes["registered-repo"]["name"] == "Registered Repo"
    assert scopes["registered-repo"]["registered"] is True
    assert scopes["memory-repo"]["name"] == "memory-repo"
    assert scopes["memory-repo"]["registered"] is False


@pytest.mark.asyncio
async def test_favicon_head_does_not_error(client):
    response = await client.head("/favicon.ico")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_memories_endpoint_protected(client):
    # Should fail without auth
    response = await client.get("/memories")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_memories_endpoint_with_api_key(client):
    headers = {"X-API-KEY": "test_key"}
    response = await client.get("/memories", headers=headers)
    assert response.status_code == 200
    assert isinstance(response.json(), list)


@pytest.mark.asyncio
async def test_create_memory_with_attribution(client):
    headers = {"X-API-KEY": "test_key"}
    payload = {
        "content": "Test memory with attribution",
        "layer": "episodic",
        "category": "test",
    }
    response = await client.post("/memories", json=payload, headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["content"] == payload["content"]

    # Verify memory exists and has author_id in metadata
    mem_id = data["id"]
    response = await client.get(f"/memories/{mem_id}", headers=headers)
    assert response.status_code == 200
    assert response.json()["metadata"]["author_id"] == "api_key_user"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"content": "Bad layer", "layer": "not-a-layer"},
        {"content": "Bad importance", "importance": 1.5},
        {"content": "Bad importance", "importance": -0.1},
        {"content": "", "layer": "episodic"},
    ],
)
async def test_create_memory_rejects_invalid_payloads(client, payload):
    headers = {"X-API-KEY": "test_key"}

    response = await client.post("/memories", json=payload, headers=headers)

    assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"importance": 2},
        {"importance": -1},
        {"content": ""},
    ],
)
async def test_update_memory_rejects_invalid_payloads(client, payload):
    headers = {"X-API-KEY": "test_key"}
    create_response = await client.post(
        "/memories",
        json={"content": "Update validation target"},
        headers=headers,
    )
    assert create_response.status_code == 200
    mem_id = create_response.json()["id"]

    response = await client.patch(f"/memories/{mem_id}", json=payload, headers=headers)

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_memory_responses_use_persisted_accessed_at(client):
    headers = {"X-API-KEY": "test_key"}
    create_response = await client.post(
        "/memories",
        json={"content": "Timestamp target", "layer": "episodic"},
        headers=headers,
    )
    assert create_response.status_code == 200
    mem_id = create_response.json()["id"]

    with sqlite3.connect(app.state.storage.db_path) as conn:
        conn.execute(
            "UPDATE memories SET accessed_at = ? WHERE id = ?",
            ("2000-01-01 00:00:00", mem_id),
        )
        conn.commit()

    list_response = await client.get("/memories", headers=headers)
    assert list_response.status_code == 200
    listed = next(memory for memory in list_response.json() if memory["id"] == mem_id)
    assert listed["accessed_at"] == "2000-01-01T00:00:00"

    get_response = await client.get(f"/memories/{mem_id}", headers=headers)
    assert get_response.status_code == 200
    assert get_response.json()["accessed_at"] == "2000-01-01T00:00:00"


@pytest.mark.asyncio
async def test_list_memories_filters_by_layer_and_category(client):
    headers = {"X-API-KEY": "test_key"}
    await client.post(
        "/memories",
        json={"content": "Regular event", "layer": "episodic", "category": "note"},
        headers=headers,
    )
    await client.post(
        "/memories",
        json={"content": "Fragile auth warning", "layer": "semantic", "category": "fragile_area"},
        headers=headers,
    )
    await client.post(
        "/memories",
        json={"content": "Team convention", "layer": "semantic", "category": "convention"},
        headers=headers,
    )

    response = await client.get(
        "/memories?layer=semantic&category=fragile_area",
        headers=headers,
    )

    assert response.status_code == 200
    assert [memory["content"] for memory in response.json()] == ["Fragile auth warning"]


@pytest.mark.asyncio
async def test_remember_returns_latest_visible_active_memory(client):
    headers = {"X-API-KEY": "test_key"}
    old_response = await client.post(
        "/memories",
        json={"content": "Older memory", "repo_id": "repo-a"},
        headers=headers,
    )
    latest_response = await client.post(
        "/memories",
        json={"content": "Latest memory to remember", "repo_id": "repo-a"},
        headers=headers,
    )
    archived_response = await client.post(
        "/memories",
        json={"content": "Archived newest memory", "repo_id": "repo-a"},
        headers=headers,
    )
    other_repo_response = await client.post(
        "/memories",
        json={"content": "Other repo latest memory", "repo_id": "repo-b"},
        headers=headers,
    )
    assert old_response.status_code == 200
    assert latest_response.status_code == 200
    assert archived_response.status_code == 200
    assert other_repo_response.status_code == 200

    with sqlite3.connect(app.state.storage.db_path) as conn:
        conn.execute(
            "UPDATE memories SET created_at = '2020-01-01 00:00:00' WHERE id = ?",
            (old_response.json()["id"],),
        )
        conn.execute(
            "UPDATE memories SET created_at = '2030-01-01 00:00:00' WHERE id = ?",
            (latest_response.json()["id"],),
        )
        conn.execute(
            """
            UPDATE memories
            SET created_at = '2031-01-01 00:00:00', status = 'archived'
            WHERE id = ?
            """,
            (archived_response.json()["id"],),
        )
        conn.execute(
            "UPDATE memories SET created_at = '2032-01-01 00:00:00' WHERE id = ?",
            (other_repo_response.json()["id"],),
        )
        conn.commit()

    response = await client.get("/remember?repo_id=repo-a", headers=headers)

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == latest_response.json()["id"]
    assert data["content"] == "Latest memory to remember"


@pytest.mark.asyncio
async def test_archived_memory_is_hidden_from_default_list_and_recall(client):
    headers = {"X-API-KEY": "test_key"}
    create_response = await client.post(
        "/memories",
        json={"content": "Archive target memory", "layer": "episodic"},
        headers=headers,
    )
    assert create_response.status_code == 200
    mem_id = create_response.json()["id"]
    assert create_response.json()["status"] == "active"

    patch_response = await client.patch(
        f"/memories/{mem_id}",
        json={"status": "archived"},
        headers=headers,
    )
    assert patch_response.status_code == 200

    list_response = await client.get("/memories", headers=headers)
    assert list_response.status_code == 200
    assert all(memory["id"] != mem_id for memory in list_response.json())

    archived_response = await client.get("/memories?status=archived", headers=headers)
    assert archived_response.status_code == 200
    archived = archived_response.json()
    assert [memory["id"] for memory in archived] == [mem_id]
    assert archived[0]["archived_at"] is not None

    recall_response = await client.post(
        "/recall",
        json={"query": "Archive target memory", "limit": 10},
        headers=headers,
    )
    assert recall_response.status_code == 200
    assert all(memory["id"] != mem_id for memory in recall_response.json())


@pytest.mark.asyncio
async def test_quality_duplicates_are_repo_scoped_and_exclude_archived(client):
    headers = {"X-API-KEY": "test_key"}
    duplicate = "Repeatable duplicate memory"
    first = await client.post(
        "/memories",
        json={"content": duplicate, "repo_id": "repo-a"},
        headers=headers,
    )
    second = await client.post(
        "/memories",
        json={"content": duplicate.lower(), "repo_id": "repo-a"},
        headers=headers,
    )
    archived = await client.post(
        "/memories",
        json={"content": duplicate, "repo_id": "repo-a"},
        headers=headers,
    )
    await client.post(
        "/memories",
        json={"content": duplicate, "repo_id": "repo-b"},
        headers=headers,
    )
    await client.patch(
        f"/memories/{archived.json()['id']}",
        json={"status": "archived"},
        headers=headers,
    )

    response = await client.get("/quality/duplicates?repo_id=repo-a", headers=headers)

    assert response.status_code == 200
    candidates = response.json()["candidates"]
    assert len(candidates) == 1
    assert set(candidates[0]["ids"]) == {first.json()["id"], second.json()["id"]}


@pytest.mark.asyncio
async def test_quality_decay_preview_indicates_memories_that_would_decay(client):
    headers = {"X-API-KEY": "test_key"}
    old_response = await client.post(
        "/memories",
        json={"content": "Old unused memory", "repo_id": "repo-a", "importance": 0.8},
        headers=headers,
    )
    recent_response = await client.post(
        "/memories",
        json={"content": "Recent stable memory", "repo_id": "repo-a", "importance": 0.7},
        headers=headers,
    )
    assert old_response.status_code == 200
    assert recent_response.status_code == 200

    old_id = old_response.json()["id"]
    recent_id = recent_response.json()["id"]
    with app.state.storage._get_db() as conn:
        conn.execute(
            "UPDATE memories SET accessed_at = '2020-01-01 00:00:00' WHERE id = ?",
            (old_id,),
        )
        conn.commit()

    response = await client.get(
        "/quality/decay-preview?repo_id=repo-a&halflife_days=30&limit=10",
        headers=headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["halflife_days"] == 30
    candidates = {item["memory_id"]: item for item in data["candidates"]}
    assert candidates[old_id]["risk"] == "likely_to_decay"
    assert candidates[old_id]["projected_importance"] < candidates[old_id]["current_importance"]
    assert candidates[old_id]["decay_amount"] > candidates[recent_id]["decay_amount"]


@pytest.mark.asyncio
async def test_audit_log_records_memory_state_changes_without_secrets(client):
    headers = {"X-API-KEY": "test_key"}
    create_response = await client.post(
        "/memories",
        json={
            "content": "Audited memory",
            "repo_id": "audit-repo",
            "metadata": {"api_key": "secret-value", "safe": "visible"},
        },
        headers=headers,
    )
    mem_id = create_response.json()["id"]
    await client.patch(
        f"/memories/{mem_id}",
        json={"status": "archived"},
        headers=headers,
    )
    await client.delete(f"/memories/{mem_id}", headers=headers)

    response = await client.get(
        "/platform/audit-log?repo_id=audit-repo",
        headers=headers,
    )

    assert response.status_code == 200
    events = response.json()
    event_types = [event["event_type"] for event in events]
    assert event_types == ["memory.deleted", "memory.archived", "memory.created"]
    assert all(event["actor_id"] == "api_key_user" for event in events)
    assert all(event["repo_id"] == "audit-repo" for event in events)
    assert "secret-value" not in str(events)


@pytest.mark.asyncio
async def test_intents_endpoint(client):
    headers = {"X-API-KEY": "test_key"}
    response = await client.get("/intents", headers=headers)
    assert response.status_code == 200
    assert isinstance(response.json(), list)


@pytest.mark.asyncio
async def test_intents_preserve_repo_id(client):
    headers = {"X-API-KEY": "test_key"}
    payload = {"description": "Scoped Docker/Codex work", "priority": 2, "repo_id": "repo-a"}

    create_response = await client.post("/intents", json=payload, headers=headers)
    assert create_response.status_code == 200
    assert create_response.json()["repo_id"] == "repo-a"

    list_response = await client.get("/intents?repo_id=repo-a", headers=headers)
    assert list_response.status_code == 200
    intents = list_response.json()
    assert any(intent["description"] == payload["description"] for intent in intents)
    assert all(intent["repo_id"] == "repo-a" for intent in intents)


@pytest.mark.asyncio
async def test_complete_intent_endpoint_marks_intent_inactive(client):
    headers = {"X-API-KEY": "test_key"}
    payload = {"description": "Ship remote done support", "priority": 2}

    create_response = await client.post("/intents", json=payload, headers=headers)
    assert create_response.status_code == 200
    intent_id = create_response.json()["id"]

    complete_response = await client.post(f"/intents/{intent_id}/complete", headers=headers)
    assert complete_response.status_code == 200
    assert complete_response.json() == {"status": "completed", "id": intent_id}

    list_response = await client.get("/intents", headers=headers)
    assert list_response.status_code == 200
    assert all(intent["id"] != intent_id for intent in list_response.json())


@pytest.mark.asyncio
async def test_intents_can_be_listed_updated_and_closed(client):
    headers = {"X-API-KEY": "test_key"}
    create_response = await client.post(
        "/intents",
        json={"description": "Original goal", "priority": 1, "repo_id": "repo-a"},
        headers=headers,
    )
    assert create_response.status_code == 200
    intent_id = create_response.json()["id"]

    update_response = await client.patch(
        f"/intents/{intent_id}",
        json={"description": "Updated goal", "priority": 3},
        headers=headers,
    )
    assert update_response.status_code == 200
    assert update_response.json()["description"] == "Updated goal"
    assert update_response.json()["priority"] == 3

    close_response = await client.post(f"/intents/{intent_id}/close", headers=headers)
    assert close_response.status_code == 200
    assert close_response.json() == {"status": "closed", "id": intent_id}

    active_response = await client.get("/intents?repo_id=repo-a", headers=headers)
    assert active_response.status_code == 200
    assert all(intent["id"] != intent_id for intent in active_response.json())

    closed_response = await client.get(
        "/intents?repo_id=repo-a&status=closed",
        headers=headers,
    )
    assert closed_response.status_code == 200
    assert closed_response.json()[0]["id"] == intent_id
    assert closed_response.json()[0]["status"] == "closed"


@pytest.mark.asyncio
async def test_complete_intent_endpoint_returns_404_for_missing_intent(client):
    headers = {"X-API-KEY": "test_key"}

    response = await client.post("/intents/missing/complete", headers=headers)

    assert response.status_code == 404
    assert response.json()["detail"] == "Intent not found"


@pytest.mark.asyncio
async def test_recall_endpoint(client):
    headers = {"X-API-KEY": "test_key"}
    # First create a memory to recall
    await client.post(
        "/memories",
        json={"content": "Recall target", "layer": "episodic"},
        headers=headers,
    )

    payload = {"query": "target", "limit": 10}
    response = await client.post("/recall", json=payload, headers=headers)
    assert response.status_code == 200
    assert len(response.json()) > 0
    assert response.json()[0]["content"] == "Recall target"
    assert "similarity" in response.json()[0]
    assert "relevance_score" in response.json()[0]


@pytest.mark.asyncio
async def test_recall_endpoint_filters_low_relevance_results_by_default(client):
    headers = {"X-API-KEY": "test_key"}
    await client.post(
        "/memories",
        json={"content": "OpenRouter cloud embeddings power project recall", "layer": "semantic"},
        headers=headers,
    )

    response = await client.post(
        "/recall",
        json={"query": "banana bread recipe", "limit": 10},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_ask_memory_returns_scoped_citations(client):
    headers = {"X-API-KEY": "test_key"}
    await client.post(
        "/memories",
        json={
            "content": "Use OpenRouter embeddings for cloud recall",
            "layer": "semantic",
            "repo_id": "repo-a",
        },
        headers=headers,
    )
    await client.post(
        "/memories",
        json={
            "content": "Repo B uses a different provider",
            "layer": "semantic",
            "repo_id": "repo-b",
        },
        headers=headers,
    )

    response = await client.post(
        "/ai/ask",
        json={"query": "Which embeddings power cloud recall?", "repo_id": "repo-a"},
        headers=headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["mode"] == "retrieval_only"
    assert data["provider_status"] == "not_configured"
    assert len(data["citations"]) == 1
    assert data["citations"][0]["repo_id"] == "repo-a"
    assert data["citations"][0]["memory_id"] in data["answer"]
    assert "Repo B" not in data["answer"]


@pytest.mark.asyncio
async def test_ask_memory_excludes_archived_memories(client):
    headers = {"X-API-KEY": "test_key"}
    create_response = await client.post(
        "/memories",
        json={"content": "Archived answer source", "layer": "semantic"},
        headers=headers,
    )
    mem_id = create_response.json()["id"]
    await client.patch(
        f"/memories/{mem_id}",
        json={"status": "archived"},
        headers=headers,
    )

    response = await client.post(
        "/ai/ask",
        json={"query": "Archived answer source"},
        headers=headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["citations"] == []
    assert "No active memories matched" in data["answer"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"query": "", "limit": 10},
        {"query": "target", "limit": 0},
        {"query": "target", "limit": 201},
        {"query": "target", "layers": ["not-a-layer"]},
        {"query": "target", "min_score": 1.1},
    ],
)
async def test_recall_rejects_invalid_payloads(client, payload):
    headers = {"X-API-KEY": "test_key"}

    response = await client.post("/recall", json=payload, headers=headers)

    assert response.status_code == 422


def test_relationships_post_route_registered_once():
    matching_routes = [
        route
        for route in app.router.routes
        if route.path == "/relationships" and "POST" in (getattr(route, "methods", set()) or set())
    ]

    assert len(matching_routes) == 1


@pytest.mark.asyncio
async def test_relationships_endpoint_returns_created_contract(client):
    headers = {"X-API-KEY": "test_key"}
    source = (
        await client.post(
            "/memories",
            json={"content": "Source memory", "repo_id": "repo-a"},
            headers=headers,
        )
    ).json()["id"]
    target = (
        await client.post(
            "/memories",
            json={"content": "Target memory", "repo_id": "repo-a"},
            headers=headers,
        )
    ).json()["id"]

    response = await client.post(
        "/relationships",
        json={"source_id": source, "target_id": target, "relationship": "related"},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json()["status"] == "created"
    assert response.json()["id"]


@pytest.mark.asyncio
async def test_relationships_endpoint_round_trips_evidence(client):
    headers = {"X-API-KEY": "test_key"}
    source = (
        await client.post(
            "/memories",
            json={"content": "Source evidence memory", "repo_id": "repo-a"},
            headers=headers,
        )
    ).json()["id"]
    target = (
        await client.post(
            "/memories",
            json={"content": "Target evidence memory", "repo_id": "repo-a"},
            headers=headers,
        )
    ).json()["id"]

    response = await client.post(
        "/relationships",
        json={
            "source_id": source,
            "target_id": target,
            "relationship": "observed_in",
            "strength": 0.8,
            "evidence": {
                "confidence": "observed",
                "confidence_score": 0.9,
                "source": "api",
                "source_file": "tests/server/test_server_api.py",
                "source_location": "test_relationships_endpoint_round_trips_evidence",
                "reason": "The API test directly links source and target memories.",
                "created_by": "pytest",
            },
        },
        headers=headers,
    )
    assert response.status_code == 200

    list_response = await client.get("/relationships?repo_id=repo-a", headers=headers)
    assert list_response.status_code == 200
    relationship = list_response.json()[0]

    assert relationship["source_id"] == source
    assert relationship["target_id"] == target
    assert relationship["relationship"] == "observed_in"
    assert relationship["strength"] == 0.8
    assert relationship["evidence"] == {
        "confidence": "observed",
        "confidence_score": 0.9,
        "source": "api",
        "source_file": "tests/server/test_server_api.py",
        "source_location": "test_relationships_endpoint_round_trips_evidence",
        "reason": "The API test directly links source and target memories.",
        "created_by": "pytest",
        "created_at": relationship["created_at"],
    }


@pytest.mark.asyncio
async def test_relationships_endpoint_rejects_invalid_evidence_confidence(client):
    headers = {"X-API-KEY": "test_key"}

    response = await client.post(
        "/relationships",
        json={
            "source_id": "source",
            "target_id": "target",
            "relationship": "related",
            "evidence": {"confidence": "trusted"},
        },
        headers=headers,
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_graph_endpoint_filters_relationships_by_repo(client):
    headers = {"X-API-KEY": "test_key"}
    mem_a = (
        await client.post(
            "/memories",
            json={"content": "Repo A memory", "repo_id": "repo-a"},
            headers=headers,
        )
    ).json()["id"]
    mem_b = (
        await client.post(
            "/memories",
            json={"content": "Repo B memory", "repo_id": "repo-b"},
            headers=headers,
        )
    ).json()["id"]
    mem_b2 = (
        await client.post(
            "/memories",
            json={"content": "Repo B related memory", "repo_id": "repo-b"},
            headers=headers,
        )
    ).json()["id"]

    cross_response = await client.post(
        "/relationships",
        json={"source_id": mem_a, "target_id": mem_b, "relationship": "cross"},
        headers=headers,
    )
    assert cross_response.status_code == 400
    await client.post(
        "/relationships",
        json={
            "source_id": mem_b,
            "target_id": mem_b2,
            "relationship": "same",
            "evidence": {
                "confidence": "manual",
                "confidence_score": 0.7,
                "source": "api",
                "reason": "Repo B graph fixture links the memories.",
            },
        },
        headers=headers,
    )

    response = await client.get("/graph?repo_id=repo-b", headers=headers)
    assert response.status_code == 200
    graph = response.json()
    assert {node["id"] for node in graph["nodes"]} == {mem_b, mem_b2}
    assert len(graph["links"]) == 1
    assert graph["links"][0]["label"] == "same"
    assert graph["links"][0]["evidence"]["confidence"] == "manual"
    assert graph["links"][0]["evidence"]["confidence_score"] == 0.7
    assert graph["links"][0]["evidence"]["source"] == "api"
    assert graph["links"][0]["evidence"]["reason"] == "Repo B graph fixture links the memories."


@pytest.mark.asyncio
async def test_graph_recall_trace_endpoint_returns_evidence(client):
    headers = {"X-API-KEY": "test_key"}
    source = (
        await client.post(
            "/memories",
            json={"content": "Graph recall auth route memory", "repo_id": "repo-a"},
            headers=headers,
        )
    ).json()["id"]
    target = (
        await client.post(
            "/memories",
            json={"content": "Graph recall repository scope evidence", "repo_id": "repo-a"},
            headers=headers,
        )
    ).json()["id"]
    await client.post(
        "/relationships",
        json={
            "source_id": source,
            "target_id": target,
            "relationship": "supports",
            "strength": 0.8,
            "evidence": {
                "confidence": "observed",
                "confidence_score": 0.9,
                "source": "api",
                "reason": "The source memory supports the repository scope evidence.",
            },
        },
        headers=headers,
    )

    response = await client.post(
        "/graph-recall/trace",
        json={
            "query": "graph recall auth route",
            "repo_id": "repo-a",
            "depth": 1,
            "token_budget": 1000,
            "limit": 2,
        },
        headers=headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["mode"] == "trace"
    assert {node["id"] for node in data["nodes"]} >= {source, target}
    assert data["edges"][0]["relationship"] == "supports"
    assert data["edges"][0]["evidence"]["confidence"] == "observed"
    assert data["edges"][0]["reason"] == "The source memory supports the repository scope evidence."
