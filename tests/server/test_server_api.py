import sqlite3

import pytest

from visp_memory.core.neo4j_storage import Neo4jStorage
from visp_memory.core.trust import Provenance, assess, provenance_of, provenance_tag
from visp_memory.server import app as server_app
from visp_memory.server.app import app


async def _http_evidence(client, headers, repo_id, content):
    response = await client.post(
        "/evidence",
        json={"content": content, "repo_id": repo_id, "evidence_type": "test"},
        headers=headers,
    )
    assert response.status_code == 200
    return response.json()["id"]


def _store_semantic(content, repo_id, **kwargs):
    evidence_id = app.state.storage.store_evidence(content, repo_id=repo_id)
    return app.state.storage.store_memory(
        content,
        layer="semantic",
        repo_id=repo_id,
        evidence_ids=[evidence_id],
        **kwargs,
    )


@pytest.mark.asyncio
async def test_root_endpoint(client):
    response = await client.get("/?repo_id=repo-a", headers={"X-API-KEY": "test_key"})
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

    response = await client.get("/?repo_id=repo-a", headers=headers)

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
    assert set(data) == {"status"}


@pytest.mark.asyncio
async def test_readyz_reports_runtime_readiness(client, tmp_path, monkeypatch):
    monkeypatch.setattr(server_app, "STATIC_DIR", tmp_path)

    response = await client.get("/readyz")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"
    assert data["checks"] == {"storage": "ok", "dashboard": "ok"}


@pytest.mark.asyncio
async def test_readyz_returns_503_when_dashboard_static_is_missing(client, tmp_path, monkeypatch):
    monkeypatch.setattr(server_app, "STATIC_DIR", tmp_path / "missing")

    response = await client.get("/readyz")

    assert response.status_code == 503
    data = response.json()
    assert data["status"] == "not_ready"
    assert data["checks"] == {"storage": "ok", "dashboard": "failed"}


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
    assert data["checks"] == {"storage": "failed", "dashboard": "ok"}
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
async def test_root_requires_authentication_and_returns_request_id(client):
    response = await client.get("/")

    assert response.status_code == 401
    assert response.headers["X-Request-ID"]


@pytest.mark.asyncio
async def test_related_memories_and_sessions_round_trip(client):
    headers = {"X-API-KEY": "test_key"}
    source = await client.post(
        "/memories",
        json={"content": "Source", "repo_id": "repo-a"},
        headers=headers,
    )
    target = await client.post(
        "/memories",
        json={"content": "Target", "repo_id": "repo-a"},
        headers=headers,
    )
    source_id = source.json()["id"]
    target_id = target.json()["id"]
    relationship = await client.post(
        "/relationships",
        json={
            "source_id": source_id,
            "target_id": target_id,
            "relationship": "supports",
            "strength": 0.9,
        },
        headers=headers,
    )
    assert relationship.status_code == 200

    related = await client.get(f"/memories/{source_id}/related", headers=headers)
    assert related.status_code == 200
    assert related.json() == []

    started = await client.post("/sessions", headers=headers)
    assert started.status_code == 200
    session_id = started.json()["id"]
    completed = await client.post(
        f"/sessions/{session_id}/complete",
        json={"summary": "Done", "memory_ids": [source_id]},
        headers=headers,
    )
    assert completed.status_code == 200
    assert completed.json() == {"id": session_id, "status": "completed"}


@pytest.mark.asyncio
async def test_related_memories_filter_quarantine_with_trusted_control(client):
    headers = {"X-API-KEY": "test_key"}
    source_id = app.state.storage.store_memory(
        "Trusted related source",
        repo_id="repo-a",
        tags=[provenance_tag(Provenance.DERIVED)],
        auto_link=False,
    )
    trusted_id = app.state.storage.store_memory(
        "Trusted related target",
        repo_id="repo-a",
        tags=[provenance_tag(Provenance.DERIVED)],
        auto_link=False,
    )
    quarantined_id = app.state.storage.store_memory(
        "Poison related target",
        repo_id="repo-a",
        importance=1.0,
        tags=[provenance_tag(Provenance.EXTERNAL)],
        auto_link=False,
    )
    app.state.storage.add_relationship(source_id, trusted_id, "supports")
    app.state.storage.add_relationship(source_id, quarantined_id, "supports")

    response = await client.get(f"/memories/{source_id}/related", headers=headers)

    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [trusted_id]
    assert quarantined_id not in {item["id"] for item in response.json()}


@pytest.mark.asyncio
async def test_related_memories_filter_temporal_and_runtime_scope(client):
    headers = {"X-API-KEY": "test_key"}
    source_id = app.state.storage.store_memory(
        "Scoped related source",
        repo_id="repo-a",
        tags=[provenance_tag(Provenance.DERIVED)],
        auto_link=False,
    )

    def target(content, metadata):
        memory_id = app.state.storage.store_memory(
            content,
            repo_id="repo-a",
            tags=[provenance_tag(Provenance.DERIVED)],
            metadata=metadata,
            auto_link=False,
        )
        app.state.storage.add_relationship(source_id, memory_id, "supports")
        return memory_id

    visible = target(
        "Production deployment related target",
        {"environment": "prod", "task_type": "deploy"},
    )
    target(
        "Development deployment related target",
        {"environment": "dev", "task_type": "deploy"},
    )
    target(
        "Expired production related target",
        {
            "environment": "prod",
            "task_type": "deploy",
            "valid_to": "2026-01-15T12:00:00+00:00",
        },
    )

    response = await client.get(
        f"/memories/{source_id}/related",
        params={
            "environment": "prod",
            "task_type": "deploy",
            "as_of": "2026-01-15T12:00:00+00:00",
        },
        headers=headers,
    )

    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [visible]

    ineligible_source = app.state.storage.store_memory(
        "Development-only related source",
        repo_id="repo-a",
        tags=[provenance_tag(Provenance.DERIVED)],
        metadata={"environment": "dev"},
        auto_link=False,
    )
    universal_target = app.state.storage.store_memory(
        "Universal target must not bridge from an ineligible source",
        repo_id="repo-a",
        tags=[provenance_tag(Provenance.DERIVED)],
        auto_link=False,
    )
    app.state.storage.add_relationship(ineligible_source, universal_target, "supports")

    blocked = await client.get(
        f"/memories/{ineligible_source}/related",
        params={"environment": "prod"},
        headers=headers,
    )

    assert blocked.status_code == 200
    assert blocked.json() == []


@pytest.mark.asyncio
async def test_favicon_head_does_not_error(client):
    response = await client.head("/favicon.ico")
    assert response.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "location"),
    [("/auth", "/dashboard/auth"), ("/settings", "/dashboard/settings")],
)
async def test_bare_dashboard_utility_routes_redirect(client, path, location):
    response = await client.get(path)

    assert response.status_code == 307
    assert response.headers["location"] == location


@pytest.mark.asyncio
async def test_memories_endpoint_protected(client):
    # Should fail without auth
    response = await client.get("/memories")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_memories_endpoint_with_api_key(client):
    headers = {"X-API-KEY": "test_key"}
    response = await client.get("/memories?repo_id=repo-a", headers=headers)
    assert response.status_code == 200
    assert isinstance(response.json(), list)


@pytest.mark.asyncio
async def test_create_memory_with_attribution(client):
    headers = {"X-API-KEY": "test_key"}
    payload = {
        "content": "Test memory with attribution",
        "layer": "episodic",
        "category": "test",
        "repo_id": "repo-a",
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
async def test_http_semantic_belief_requires_existing_same_repo_evidence(client):
    headers = {"X-API-KEY": "test_key"}

    missing = await client.post(
        "/memories",
        json={
            "content": "Authentication requires secure cookies",
            "layer": "semantic",
            "repo_id": "repo-a",
        },
        headers=headers,
    )
    assert missing.status_code == 422

    evidence_response = await client.post(
        "/evidence",
        json={
            "content": "Observed Set-Cookie with Secure and HttpOnly",
            "repo_id": "repo-a",
            "evidence_type": "tool_output",
        },
        headers=headers,
    )
    assert evidence_response.status_code == 200
    evidence = evidence_response.json()
    assert evidence["content_hash"]
    assert evidence["provenance"] == "external"

    created = await client.post(
        "/memories",
        json={
            "content": "Authentication requires secure cookies",
            "layer": "semantic",
            "repo_id": "repo-a",
            "evidence_ids": [evidence["id"]],
        },
        headers=headers,
    )
    assert created.status_code == 200
    assert created.json()["evidence_ids"] == [evidence["id"]]

    fetched = await client.get(f"/evidence/{evidence['id']}", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json() == evidence

    immutable = await client.patch(
        f"/evidence/{evidence['id']}",
        json={"content": "Changed"},
        headers=headers,
    )
    assert immutable.status_code == 409


@pytest.mark.asyncio
async def test_http_evidence_redacts_secret_before_response_and_storage(client):
    secret = "sk-proj-abcdefghijklmnopqrstuvwxyz123456"
    response = await client.post(
        "/evidence",
        json={"content": f"Observed credential {secret}", "repo_id": "repo-a"},
        headers={"X-API-KEY": "test_key"},
    )

    assert response.status_code == 200
    evidence = response.json()
    assert secret not in evidence["content"]
    assert "[REDACTED:openai-key]" in evidence["content"]
    assert secret not in app.state.storage.get_evidence(evidence["id"])["content"]


@pytest.mark.asyncio
async def test_http_belief_rejects_cross_repo_evidence_without_partial_write(client):
    headers = {"X-API-KEY": "test_key"}
    evidence = (
        await client.post(
            "/evidence",
            json={"content": "Repository B output", "repo_id": "repo-b"},
            headers=headers,
        )
    ).json()

    response = await client.post(
        "/memories",
        json={
            "content": "Repository A belief must fail",
            "layer": "semantic",
            "repo_id": "repo-a",
            "evidence_ids": [evidence["id"]],
        },
        headers=headers,
    )

    assert response.status_code == 409
    repo_a = await client.get(
        "/memories",
        params={"repo_id": "repo-a", "layer": "semantic"},
        headers=headers,
    )
    assert all(item["content"] != "Repository A belief must fail" for item in repo_a.json())


@pytest.mark.asyncio
async def test_http_attach_evidence_is_atomic_and_same_repo(client):
    headers = {"X-API-KEY": "test_key"}
    first = await _http_evidence(client, headers, "repo-a", "First observation")
    second = await _http_evidence(client, headers, "repo-a", "Second observation")
    other_repo = await _http_evidence(client, headers, "repo-b", "Other observation")
    belief = await client.post(
        "/memories",
        json={
            "content": "Evidence-backed reconciliation target",
            "layer": "semantic",
            "repo_id": "repo-a",
            "evidence_ids": [first],
        },
        headers=headers,
    )
    belief_id = belief.json()["id"]

    attached = await client.post(
        f"/memories/{belief_id}/evidence",
        json={"repo_id": "repo-a", "evidence_ids": [second]},
        headers=headers,
    )
    assert attached.status_code == 200
    assert attached.json()["evidence_ids"] == [first, second]

    duplicate = await client.post(
        f"/memories/{belief_id}/evidence",
        json={"repo_id": "repo-a", "evidence_ids": [second]},
        headers=headers,
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["evidence_ids"] == [first, second]

    rejected = await client.post(
        f"/memories/{belief_id}/evidence",
        json={"repo_id": "repo-a", "evidence_ids": [second, other_repo]},
        headers=headers,
    )
    assert rejected.status_code == 409
    assert app.state.storage.get_memory(belief_id)["evidence_ids"] == [first, second]


@pytest.mark.asyncio
async def test_http_neo_governed_write_returns_explicit_unsupported(client):
    storage = Neo4jStorage.__new__(Neo4jStorage)
    storage.get_repository = lambda _repo_id: None
    storage._embedding_fn = None
    storage._uses_noop_embeddings = False
    original_storage = app.state.storage
    app.state.storage = storage
    try:
        response = await client.post(
            "/memories",
            json={"content": "Neo governed write", "repo_id": "repo-a"},
            headers={"X-API-KEY": "test_key"},
        )
    finally:
        app.state.storage = original_storage

    assert response.status_code == 501
    assert "Evidence graph" in response.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.parametrize("claimed_tag", ["provenance:authored", "provenance:not-a-tier"])
async def test_http_memory_write_replaces_self_claimed_provenance_with_external(
    client, claimed_tag
):
    headers = {"X-API-KEY": "test_key"}

    response = await client.post(
        "/memories",
        json={
            "content": f"Untrusted API memory {claimed_tag}",
            "repo_id": "repo-a",
            "tags": [claimed_tag, "keep-me"],
            "source": "authored",
        },
        headers=headers,
    )

    assert response.status_code == 200
    assert provenance_of(response.json()) is Provenance.EXTERNAL
    assert response.json()["source"] == "external"
    assert "keep-me" in response.json()["tags"]
    assert response.json()["metadata"]["write_channel"] == "http"
    stored = app.state.storage.get_memory(response.json()["id"])
    assert provenance_of(stored) is Provenance.EXTERNAL
    assert assess(stored).injectable is False


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
        json={"content": "Update validation target", "repo_id": "repo-a"},
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
        json={"content": "Timestamp target", "layer": "episodic", "repo_id": "repo-a"},
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

    list_response = await client.get("/memories?repo_id=repo-a", headers=headers)
    assert list_response.status_code == 200
    listed = next(memory for memory in list_response.json() if memory["id"] == mem_id)
    assert listed["accessed_at"] == "2000-01-01T00:00:00"

    get_response = await client.get(f"/memories/{mem_id}", headers=headers)
    assert get_response.status_code == 200
    assert get_response.json()["accessed_at"] == "2000-01-01T00:00:00"


@pytest.mark.asyncio
async def test_list_memories_filters_by_layer_and_category(client):
    headers = {"X-API-KEY": "test_key"}
    fragile_evidence = await _http_evidence(
        client, headers, "repo-a", "Fragile auth warning"
    )
    convention_evidence = await _http_evidence(
        client, headers, "repo-a", "Team convention"
    )
    await client.post(
        "/memories",
        json={
            "content": "Regular event",
            "layer": "episodic",
            "category": "note",
            "repo_id": "repo-a",
            "evidence_ids": [fragile_evidence],
        },
        headers=headers,
    )
    await client.post(
        "/memories",
        json={
            "content": "Fragile auth warning",
            "layer": "semantic",
            "category": "negative",
            "repo_id": "repo-a",
            "evidence_ids": [convention_evidence],
        },
        headers=headers,
    )
    await client.post(
        "/memories",
        json={
            "content": "Team convention",
            "layer": "semantic",
            "category": "preference",
            "repo_id": "repo-a",
        },
        headers=headers,
    )

    response = await client.get(
        "/memories?repo_id=repo-a&layer=semantic&category=fragile_area",
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
        json={"content": "Archive target memory", "layer": "episodic", "repo_id": "repo-a"},
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

    list_response = await client.get("/memories?repo_id=repo-a", headers=headers)
    assert list_response.status_code == 200
    assert all(memory["id"] != mem_id for memory in list_response.json())

    archived_response = await client.get(
        "/memories?repo_id=repo-a&status=archived", headers=headers
    )
    assert archived_response.status_code == 200
    archived = archived_response.json()
    assert [memory["id"] for memory in archived] == [mem_id]
    assert archived[0]["archived_at"] is not None

    recall_response = await client.post(
        "/recall",
        json={"query": "Archive target memory", "limit": 10, "repo_id": "repo-a"},
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
async def test_memory_delete_is_recoverable_and_purge_is_explicit(client):
    headers = {"X-API-KEY": "test_key"}
    created = await client.post(
        "/memories",
        json={"content": "Recoverable note", "repo_id": "trash-repo"},
        headers=headers,
    )
    memory_id = created.json()["id"]

    deleted = await client.delete(f"/memories/{memory_id}?reason=obsolete", headers=headers)
    assert deleted.status_code == 200
    assert deleted.json()["recoverable"] is True
    deleted_memory = await client.get(f"/memories/{memory_id}", headers=headers)
    assert deleted_memory.json()["status"] == "deleted"

    restored = await client.post(f"/memories/{memory_id}/restore", headers=headers)
    assert restored.status_code == 200
    active_memory = await client.get(f"/memories/{memory_id}", headers=headers)
    assert active_memory.json()["status"] == "active"

    await client.delete(f"/memories/{memory_id}", headers=headers)
    wrong_confirmation = await client.delete(
        f"/memories/{memory_id}/purge?confirmation=wrong", headers=headers
    )
    assert wrong_confirmation.status_code == 400
    purged = await client.delete(
        f"/memories/{memory_id}/purge?confirmation={memory_id}", headers=headers
    )
    assert purged.status_code == 200
    assert (await client.get(f"/memories/{memory_id}", headers=headers)).status_code == 404


@pytest.mark.asyncio
async def test_memory_merge_preview_execute_and_undo(client):
    headers = {"X-API-KEY": "test_key"}
    memory_ids = []
    for tags in (["one"], ["two"]):
        response = await client.post(
            "/memories",
            json={
                "content": "Canonical deployment rule",
                "repo_id": "merge-repo",
                "tags": tags,
            },
            headers=headers,
        )
        memory_ids.append(response.json()["id"])

    preview = await client.post(
        "/memories/merge/preview", json={"memory_ids": memory_ids}, headers=headers
    )
    assert preview.status_code == 200
    assert preview.json()["exact_duplicate"] is True
    merged = await client.post(
        "/memories/merge", json={"memory_ids": memory_ids}, headers=headers
    )
    assert merged.status_code == 200
    operation_id = merged.json()["operation_id"]
    merged_source = await client.get(f"/memories/{memory_ids[1]}", headers=headers)
    assert merged_source.json()["status"] == "merged"

    undone = await client.post(
        f"/memories/merge/{operation_id}/undo", headers=headers
    )
    assert undone.status_code == 200
    restored_source = await client.get(f"/memories/{memory_ids[1]}", headers=headers)
    assert restored_source.json()["status"] == "active"


@pytest.mark.asyncio
async def test_intents_endpoint(client):
    headers = {"X-API-KEY": "test_key"}
    response = await client.get("/intents?repo_id=repo-a", headers=headers)
    assert response.status_code == 200
    assert isinstance(response.json(), list)


@pytest.mark.asyncio
async def test_captured_completion_language_only_produces_advisory_evaluation(client):
    headers = {"X-API-KEY": "test_key"}
    intent = await client.post(
        "/intents",
        json={"description": "Implement secure login", "repo_id": "intent-auto"},
        headers=headers,
    )
    evidence = await client.post(
        "/memories",
        json={
            "content": "Implemented secure login and all tests passed",
            "repo_id": "intent-auto",
            "category": "test_result",
        },
        headers=headers,
    )
    assert evidence.status_code == 200

    active = await client.get(
        "/intents?repo_id=intent-auto&status=active", headers=headers
    )
    active_intent = next(item for item in active.json() if item["id"] == intent.json()["id"])
    assert active_intent["status"] == "active"
    assert "completion_evaluation" not in active_intent["context"]

    evaluation_response = await client.post(
        f"/intents/{intent.json()['id']}/evaluate",
        json={
            "summary": "Implemented secure login and all tests passed",
            "memory_ids": [evidence.json()["id"]],
            "allow_auto_complete": True,
        },
        headers=headers,
    )
    evaluation = evaluation_response.json()
    assert evaluation["decision"] == "suggested"
    assert evaluation["authoritative"] is False
    assert evaluation["status_changed"] is False


@pytest.mark.asyncio
async def test_context_compile_returns_citations_and_unchanged_delta(client):
    headers = {"X-API-KEY": "test_key"}
    evidence_id = await _http_evidence(
        client, headers, "context-repo", "Session cookies protect dashboard authentication"
    )
    await client.post(
        "/memories",
        json={
            "content": "Session cookies protect dashboard authentication",
            "layer": "semantic",
            "repo_id": "context-repo",
            "files": ["src/auth.py"],
            "symbols": ["login"],
            "confidence": 0.95,
            "source_revision": "abc123",
            "evidence_ids": [evidence_id],
        },
        headers=headers,
    )
    compiled = await client.post(
        "/context/compile",
        json={
            "query": "dashboard authentication",
            "repo_id": "context-repo",
            "files": ["src/auth.py"],
            "token_budget": 200,
        },
        headers=headers,
    )
    assert compiled.status_code == 200
    assert compiled.json()["items"][0]["source_revision"] == "abc123"

    unchanged = await client.post(
        "/context/compile",
        json={
            "query": "dashboard authentication",
            "repo_id": "context-repo",
            "files": ["src/auth.py"],
            "token_budget": 200,
            "previous_fingerprint": compiled.json()["fingerprint"],
        },
        headers=headers,
    )
    assert unchanged.json()["unchanged"] is True
    assert unchanged.json()["token_count"] == 0


@pytest.mark.asyncio
async def test_task_memory_brief_returns_sections_unknowns_and_delta(client):
    headers = {"X-API-KEY": "test_key"}
    evidence_id = await _http_evidence(
        client,
        headers,
        "brief-repo",
        "WARNING [auth]: preserve legacy API compatibility",
    )
    memory = await client.post(
        "/memories",
        json={
            "content": "WARNING [auth]: preserve legacy API compatibility",
            "layer": "semantic",
            "category": "negative",
            "repo_id": "brief-repo",
            "files": ["src/auth.py"],
            "confidence": 0.96,
            "source_revision": "brief123",
            "evidence_ids": [evidence_id],
        },
        headers=headers,
    )
    assert memory.status_code == 200
    trusted_id = _store_semantic(
        "WARNING [auth]: trusted legacy API compatibility",
        "brief-repo",
        category="negative",
        tags=[provenance_tag(Provenance.DERIVED)],
        metadata={
            "files": ["src/auth.py"],
            "confidence": 0.96,
            "source_revision": "brief123",
        },
        auto_link=False,
    )
    response = await client.post(
        "/context/brief",
        json={
            "task": "Update `src/auth.py` authentication",
            "repo_id": "brief-repo",
            "constraints": ["Do not expose credentials"],
            "token_budget": 300,
        },
        headers=headers,
    )
    assert response.status_code == 200
    brief = response.json()
    assert brief["sections"]["warnings"][0]["id"] == trusted_id
    assert memory.json()["id"] not in {
        citation["memory_id"] for citation in brief["citations"]
    }
    assert brief["citations"][0]["source_revision"] == "brief123"
    assert brief["token_count"] <= 300
    assert any("No active intent" in unknown for unknown in brief["unknowns"])

    unchanged = await client.post(
        "/context/brief",
        json={
            "task": "Update `src/auth.py` authentication",
            "repo_id": "brief-repo",
            "constraints": ["Do not expose credentials"],
            "token_budget": 300,
            "previous_fingerprint": brief["fingerprint"],
        },
        headers=headers,
    )
    assert unchanged.status_code == 200
    assert unchanged.json()["unchanged"] is True
    assert unchanged.json()["context"] == ""


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
async def test_complete_intent_endpoint_records_outcome_without_status_change(client):
    headers = {"X-API-KEY": "test_key"}
    payload = {"description": "Ship remote done support", "priority": 2, "repo_id": "repo-a"}

    create_response = await client.post("/intents", json=payload, headers=headers)
    assert create_response.status_code == 200
    intent_id = create_response.json()["id"]

    complete_response = await client.post(f"/intents/{intent_id}/complete", headers=headers)
    assert complete_response.status_code == 200
    assert complete_response.json() == {
        "id": intent_id,
        "status": "active",
        "authoritative": False,
        "status_changed": False,
        "outcome_recorded": True,
    }

    list_response = await client.get("/intents?repo_id=repo-a", headers=headers)
    assert list_response.status_code == 200
    refreshed = next(intent for intent in list_response.json() if intent["id"] == intent_id)
    outcome = refreshed["context"]["outcome_history"][-1]
    assert outcome["outcome"] == "completed"
    assert outcome["provenance"]["source"] == "external"
    assert outcome["provenance"]["channel"] == "rest"
    assert outcome["status_changed"] is False


@pytest.mark.asyncio
async def test_intents_can_be_listed_updated_and_close_is_history_only(client):
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
    assert close_response.json()["status"] == "active"
    assert close_response.json()["status_changed"] is False

    active_response = await client.get("/intents?repo_id=repo-a", headers=headers)
    assert active_response.status_code == 200
    assert any(intent["id"] == intent_id for intent in active_response.json())

    closed_response = await client.get(
        "/intents?repo_id=repo-a&status=closed",
        headers=headers,
    )
    assert closed_response.status_code == 200
    assert closed_response.json() == []


@pytest.mark.asyncio
async def test_generic_intent_status_update_is_rejected(client):
    headers = {"X-API-KEY": "test_key"}
    created = await client.post(
        "/intents",
        json={"description": "Keep status external", "repo_id": "repo-a"},
        headers=headers,
    )

    response = await client.patch(
        f"/intents/{created.json()['id']}",
        json={"status": "completed"},
        headers=headers,
    )

    assert response.status_code == 409
    assert "does not change intent status" in response.json()["detail"]
    active = await client.get("/intents?repo_id=repo-a", headers=headers)
    assert any(item["id"] == created.json()["id"] for item in active.json())


@pytest.mark.asyncio
async def test_complete_intent_endpoint_returns_404_for_missing_intent(client):
    headers = {"X-API-KEY": "test_key"}

    response = await client.post("/intents/missing/complete", headers=headers)

    assert response.status_code == 404
    assert response.json()["detail"] == "Intent not found"


@pytest.mark.asyncio
async def test_reopen_endpoint_preserves_historical_status_and_records_history(client):
    headers = {"X-API-KEY": "test_key"}
    created = await client.post(
        "/intents",
        json={"description": "Historical completed goal", "repo_id": "repo-a"},
        headers=headers,
    )
    intent_id = created.json()["id"]
    with app.state.storage._get_db() as conn:
        conn.execute("UPDATE intents SET status = 'completed' WHERE id = ?", (intent_id,))
        conn.commit()

    reopened = await client.post(f"/intents/{intent_id}/reopen", headers=headers)

    assert reopened.status_code == 200
    assert reopened.json()["status"] == "completed"
    assert reopened.json()["status_changed"] is False
    historical = await client.get(
        "/intents?repo_id=repo-a&status=completed",
        headers=headers,
    )
    stored = next(item for item in historical.json() if item["id"] == intent_id)
    assert stored["status"] == "completed"
    outcome = stored["context"]["outcome_history"][-1]
    assert outcome["outcome"] == "active"
    assert outcome["provenance"]["source"] == "external"
    assert outcome["provenance"]["channel"] == "rest"
    assert outcome["status_changed"] is False


@pytest.mark.asyncio
async def test_recall_endpoint(client):
    headers = {"X-API-KEY": "test_key"}
    # First create a memory to recall
    await client.post(
        "/memories",
        json={"content": "Recall target", "layer": "episodic", "repo_id": "repo-a"},
        headers=headers,
    )

    payload = {"query": "target", "limit": 10, "repo_id": "repo-a"}
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
        json={
            "content": "OpenRouter cloud embeddings power project recall",
            "layer": "semantic",
            "repo_id": "repo-a",
        },
        headers=headers,
    )

    response = await client.post(
        "/recall",
        json={"query": "banana bread recipe", "limit": 10, "repo_id": "repo-a"},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_ask_memory_returns_scoped_citations(client):
    headers = {"X-API-KEY": "test_key"}
    _store_semantic(
        "Use OpenRouter embeddings for cloud recall",
        "repo-a",
        tags=[provenance_tag(Provenance.DERIVED)],
        auto_link=False,
    )
    _store_semantic(
        "Repo B uses a different provider",
        "repo-b",
        tags=[provenance_tag(Provenance.DERIVED)],
        auto_link=False,
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
async def test_ask_memory_filters_trust_temporal_and_runtime_scope(client):
    headers = {"X-API-KEY": "test_key"}

    def store(content, metadata, tier=Provenance.DERIVED):
        return _store_semantic(
            content,
            "repo-a",
            tags=[provenance_tag(tier)],
            metadata=metadata,
            auto_link=False,
        )

    visible = store(
        "Scoped deployment answer evidence",
        {"environment": "prod", "task_type": "deploy"},
    )
    store(
        "Scoped deployment answer expired evidence",
        {
            "environment": "prod",
            "task_type": "deploy",
            "valid_to": "2026-01-15T12:00:00+00:00",
        },
    )
    store(
        "Scoped deployment answer dev evidence",
        {"environment": "dev", "task_type": "deploy"},
    )
    store(
        "Scoped deployment answer poison evidence",
        {"environment": "prod", "task_type": "deploy"},
        Provenance.EXTERNAL,
    )

    response = await client.post(
        "/ai/ask",
        json={
            "query": "Scoped deployment answer evidence",
            "repo_id": "repo-a",
            "environment": "prod",
            "task_type": "deploy",
            "as_of": "2026-01-15T12:00:00+00:00",
            "limit": 20,
        },
        headers=headers,
    )

    assert response.status_code == 200
    assert [item["memory_id"] for item in response.json()["citations"]] == [visible]


@pytest.mark.asyncio
async def test_ask_memory_excludes_archived_memories(client):
    headers = {"X-API-KEY": "test_key"}
    evidence_id = await _http_evidence(
        client, headers, "repo-a", "Archived answer source"
    )
    create_response = await client.post(
        "/memories",
        json={
            "content": "Archived answer source",
            "layer": "semantic",
            "repo_id": "repo-a",
            "evidence_ids": [evidence_id],
        },
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
        json={"query": "Archived answer source", "repo_id": "repo-a"},
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


def _iter_registered_routes(routes):
    """Flatten app routes across Starlette versions.

    Newer Starlette stores each included router as an opaque ``_IncludedRouter``
    whose sub-routes live on ``.original_router.routes`` (instead of flattening
    them into ``app.router.routes`` as older versions did). Recurse so route
    enumeration works on both layouts.
    """
    for route in routes:
        included = getattr(route, "original_router", None)
        if included is not None:
            yield from _iter_registered_routes(included.routes)
        else:
            yield route


def test_relationships_post_route_registered_once():
    matching_routes = [
        route
        for route in _iter_registered_routes(app.router.routes)
        if getattr(route, "path", None) == "/relationships"
        and "POST" in (getattr(route, "methods", set()) or set())
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
    source = app.state.storage.store_memory(
        "Graph recall auth route memory",
        repo_id="repo-a",
        tags=[provenance_tag(Provenance.DERIVED)],
        auto_link=False,
    )
    target = app.state.storage.store_memory(
        "Graph recall repository scope evidence",
        repo_id="repo-a",
        tags=[provenance_tag(Provenance.DERIVED)],
        auto_link=False,
    )
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


@pytest.mark.asyncio
async def test_memory_intelligence_report_endpoint_returns_public_contract(client):
    headers = {"X-API-KEY": "test_key"}
    evidence_id = await _http_evidence(
        client, headers, "repo-a", "API fragile warning"
    )
    await client.post(
        "/memories",
        json={
            "content": "High impact API memory",
            "repo_id": "repo-a",
            "importance": 0.9,
        },
        headers=headers,
    )
    await client.post(
        "/memories",
        json={
            "content": "API fragile warning",
            "repo_id": "repo-a",
            "layer": "semantic",
            "category": "negative",
            "evidence_ids": [evidence_id],
        },
        headers=headers,
    )

    response = await client.get("/reports/memory-intelligence?repo_id=repo-a", headers=headers)

    assert response.status_code == 200
    report = response.json()
    assert report["schema_version"] == "1.0"
    assert report["summary"]["total_memories"] == 2
    assert report["thresholds"]["high_impact_importance"] == 0.75
    assert report["sections"]["high_impact_memories"]["kind"] == "stored_fact"
    assert report["sections"]["fragile_areas"]["items"]
    assert report["sections"]["suggested_questions"]["kind"] == "inferred_recommendation"


@pytest.mark.asyncio
async def test_memory_intelligence_report_text_endpoint(client):
    headers = {"X-API-KEY": "test_key"}

    response = await client.get(
        "/reports/memory-intelligence/text?repo_id=repo-a", headers=headers
    )

    assert response.status_code == 200
    assert "Memory Intelligence Report" in response.text
    assert "Thresholds" in response.text
