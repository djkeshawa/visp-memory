import pytest

from visp_memory.server.app import app


def token(scopes, *, role="user"):
    account = app.state.auth_store.create_account(
        username="permission-user", password="correct-horse-battery-staple",
        role=role, team_id="alpha",
    )
    _, raw = app.state.auth_store.create_token(
        user_id=account["id"], name="permissions", scopes=scopes, repo_ids=["allowed"],
    )
    return {"Authorization": f"Bearer {raw}"}


def memories(*, team="alpha"):
    storage = app.state.storage
    storage.store_repository({"id": "allowed", "name": "Allowed", "team_id": "alpha"})
    return [
        storage.store_memory(
            f"private record {index}", repo_id="allowed",
            metadata={"team_id": team}, auto_link=False,
        )
        for index in range(2)
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["create", "delete"])
@pytest.mark.parametrize("scopes, expected", [
    (["project:read"], 403), (["memory:read"], 403), (["memory:write"], 200),
])
async def test_relationship_mutations_require_memory_write(client, operation, scopes, expected):
    storage = app.state.storage
    source, target = memories()
    headers = token(scopes)
    if operation == "create":
        response = await client.post(
            "/relationships", headers=headers,
            json={"source_id": source, "target_id": target, "relationship": "related_to"},
        )
        expected_count = int(expected == 200)
    else:
        relationship_id = storage.add_relationship(source, target, "related_to")
        response = await client.delete(f"/relationships/{relationship_id}", headers=headers)
        expected_count = int(expected != 200)
    assert response.status_code == expected
    assert len(storage.get_all_relationships(repo_id="allowed")) == expected_count


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["create", "delete"])
async def test_archived_repository_relationships_are_not_mutated(client, operation):
    storage = app.state.storage
    source, target = memories()
    relationship_id = storage.add_relationship(source, target, "related_to")
    storage.update_repository("allowed", status="archived")
    headers = {"X-API-KEY": "test_key"}
    if operation == "create":
        response = await client.post(
            "/relationships", headers=headers,
            json={"source_id": target, "target_id": source, "relationship": "related_to"},
        )
    else:
        response = await client.delete(f"/relationships/{relationship_id}", headers=headers)
    assert response.status_code == 409
    assert len(storage.get_all_relationships(repo_id="allowed")) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/relationships", "/ai/reflections"])
async def test_reduced_admin_token_cannot_read_foreign_records(client, path):
    source, target = memories(team="beta")
    app.state.storage.add_relationship(
        source, target, "related_to", evidence={"reason": "foreign private evidence"},
    )
    headers = token(["memory:read", "project:read"], role="admin")
    assert (await client.get(f"/memories/{source}", headers=headers)).status_code == 404
    response = await client.get(
        path, headers=headers, params={"repo_id": "allowed", "min_evidence": 2},
    )
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("resource", ["memory", "intent"])
async def test_reduced_admin_token_cannot_reassign_record_ownership(client, resource):
    source, _ = memories()
    storage = app.state.storage
    if resource == "memory":
        path, field, scope = f"/memories/{source}", "metadata", "memory:write"
    else:
        source = storage.set_intent(
            "private intent", repo_id="allowed", context={"team_id": "alpha"},
        )
        path, field, scope = f"/intents/{source}", "context", "intent:write"
    response = await client.patch(
        path, headers=token([scope], role="admin"),
        json={field: {"team_id": "beta", "author_id": "someone-else"}},
    )
    assert response.status_code == 200
    if resource == "memory":
        record = storage.get_memory(source)
    else:
        record = next(row for row in storage.get_active_intents() if row["id"] == source)
    assert record[field] == {"team_id": "alpha"}


@pytest.mark.asyncio
async def test_reduced_admin_token_does_not_count_foreign_memories(client):
    memories(team="beta")
    response = await client.get(
        "/status", params={"repo_id": "allowed"},
        headers=token(["project:read"], role="admin"),
    )
    assert response.status_code == 200
    assert response.json()["total_memories"] == 0


@pytest.mark.asyncio
async def test_reduced_admin_token_cannot_report_another_owners_workflow(client):
    memories()
    intent_id = app.state.storage.set_intent(
        "Owned intent", repo_id="allowed", context={"team_id": "alpha", "author_id": "owner"},
    )
    response = await client.post(
        f"/intents/{intent_id}/workflow-status",
        headers=token(["intent:write"], role="admin"),
        json={
            "source": "ci", "task_id": "task", "event_id": "event", "revision": 1,
            "status": "active", "summary": "Work started",
        },
    )
    assert response.status_code == 403
    intent = next(row for row in app.state.storage.get_active_intents() if row["id"] == intent_id)
    assert "external_workflow" not in intent["context"]
