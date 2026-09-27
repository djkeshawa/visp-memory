import copy
import hashlib
from types import SimpleNamespace

import pytest

from tests.server.test_owner_maintenance import (
    OWNER_TOKEN,
)
from tests.server.test_owner_maintenance import (
    owner_client as owner_client,
)
from visp_memory.core.owner_token import OWNER_TOKEN_HEADER
from visp_memory.core.storage import StorageCapabilities
from visp_memory.server.app import app
from visp_memory.server.auth import UserContext, get_current_user

OWNER_HEADERS = {OWNER_TOKEN_HEADER: OWNER_TOKEN}


def graph_document(repo_id=None):
    return {
        "version": "3.0",
        "memories": {
            "episodic": [{
                "id": "portable-memory",
                "content": "A portable observation",
                "repo_id": repo_id,
                "metadata": {"written_by": "original-writer", "author_id": "original"},
                "source": "authored",
            }],
        },
    }


@pytest.mark.asyncio
async def test_export_is_complete_scoped_and_does_not_count_as_recall(owner_client):
    storage = app.state.storage
    first = storage.store_memory("Visible observation", repo_id="repo-a", auto_link=False)
    storage.store_memory("Hidden observation", repo_id="repo-b", auto_link=False)
    evidence_id = storage.store_evidence("Exact supporting evidence", repo_id="repo-a")
    belief_id = storage.store_memory(
        "Supported belief", layer="semantic", category="fact", repo_id="repo-a",
        evidence_ids=[evidence_id], auto_link=False,
    )
    storage.add_relationship(first, belief_id, "supports")
    storage.set_intent("Finish the work", repo_id="repo-a")
    before = storage.peek_memory(belief_id)

    response = await owner_client.get("/repos/repo-a/export")

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["version"] == "3.0"
    assert {item["repo_id"] for rows in data["memories"].values() for item in rows} == {
        "repo-a"
    }
    assert evidence_id in {item["id"] for item in data["evidence"]}
    assert len(data["relationships"]) == 1
    assert len(data["intents"]) == 1
    assert "Hidden observation" not in response.text
    assert storage.peek_memory(belief_id) == before


@pytest.mark.asyncio
async def test_export_and_attestation_hide_other_teams(owner_client):
    storage = app.state.storage
    storage.store_repository({"id": "repo-b", "name": "Private", "team_id": "team-b"})
    memory_id = storage.store_memory("Private", repo_id="repo-b", auto_link=False)
    app.dependency_overrides[get_current_user] = lambda: UserContext(
        user_id="reader", username="reader", team_id="team-a",
    )
    try:
        for path in (
            "/repos/repo-b/export",
            f"/memories/{memory_id}/attestation?repo_id=repo-b",
        ):
            response = await owner_client.get(path)
            assert response.status_code == 404, response.text
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("record_kind", ["memory", "evidence", "intent"])
async def test_export_does_not_bypass_record_access_in_unregistered_scope(
    owner_client, record_kind,
):
    storage = app.state.storage
    if record_kind == "memory":
        storage.store_memory("Private", repo_id="repo-b", metadata={"team_id": "team-b"})
    elif record_kind == "evidence":
        storage.store_evidence("Private", repo_id="repo-b", metadata={"team_id": "team-b"})
    else:
        storage.set_intent("Private", repo_id="repo-b", context={"team_id": "team-b"})

    async def other_team():
        return UserContext(user_id="reader", username="reader", team_id="team-a")

    app.dependency_overrides[get_current_user] = other_team
    try:
        response = await owner_client.get("/repos/repo-b/export")
        assert response.status_code == 404, response.text
        assert "Private" not in response.text
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("token", [None, "invalid"])
async def test_import_cannot_grant_itself_trust_without_owner_proof(owner_client, token):
    response = await owner_client.post(
        "/repos/repo-a/import", json=graph_document("repo-a"),
        headers={} if token is None else {OWNER_TOKEN_HEADER: token},
    )
    assert response.status_code == 403
    assert app.state.storage.peek_memory("portable-memory") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("principal", ["owner", "admin"])
async def test_import_preserves_attribution_and_uses_path_for_missing_scope(
    owner_client, principal,
):
    if principal == "admin":
        app.dependency_overrides[get_current_user] = lambda: UserContext(
            user_id="admin", username="admin", is_admin=True,
        )
    try:
        response = await owner_client.post(
            "/repos/repo-a/import", json=graph_document(),
            headers=OWNER_HEADERS if principal == "owner" else {},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "completed"
    imported = app.state.storage.peek_memory("portable-memory")
    assert imported["repo_id"] == "repo-a"
    assert imported["metadata"] == graph_document()["memories"]["episodic"][0]["metadata"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "record_kind", ["episodic", "raw", "semantic", "intent", "evidence", "intents"]
)
async def test_import_rejects_cross_repo_records_before_any_write(owner_client, record_kind):
    data = graph_document("repo-a")
    foreign = {"id": "foreign", "content": "Foreign content", "repo_id": "repo-b"}
    if record_kind == "evidence":
        foreign["content_hash"] = hashlib.sha256(foreign["content"].encode()).hexdigest()
        data["evidence"] = [foreign]
    elif record_kind == "intents":
        data["intents"] = [{**foreign, "description": "Foreign goal"}]
    else:
        data["memories"].setdefault(record_kind, []).append(foreign)
    response = await owner_client.post("/repos/repo-a/import", json=data, headers=OWNER_HEADERS)
    assert response.status_code == 422, response.text
    assert "repo" in response.json()["detail"].lower()
    assert app.state.storage.peek_memory("portable-memory") is None
    assert app.state.storage.list_memories(repo_id="repo-b") == []


@pytest.mark.asyncio
async def test_import_keeps_existing_portable_evidence_remapping(owner_client):
    data = graph_document()
    data["memories"]["episodic"][0]["evidence_ids"] = ["portable-evidence"]
    content = "Original supporting evidence"
    data["evidence"] = [{
        "id": "portable-evidence", "content": content, "repo_id": "repo-b",
        "content_hash": hashlib.sha256(content.encode()).hexdigest(),
    }]
    response = await owner_client.post("/repos/repo-a/import", json=data, headers=OWNER_HEADERS)
    assert response.status_code == 200, response.text
    storage = app.state.storage
    assert storage.get_evidence("portable-evidence")["repo_id"] == "repo-a"
    assert storage.peek_memory("portable-memory")["repo_id"] == "repo-a"
    assert storage.list_evidence(repo_id="repo-b") == []


@pytest.mark.asyncio
async def test_graph_import_still_requires_evidence_and_rolls_back(owner_client):
    data = graph_document("repo-a")
    data["memories"]["semantic"] = [{
        "id": "unfounded", "repo_id": "repo-a", "content": "Unsupported claim",
        "category": "fact", "belief_type": "fact", "epistemic_status": "inferred",
    }]
    response = await owner_client.post("/repos/repo-a/import", json=data, headers=OWNER_HEADERS)
    assert response.status_code == 422, response.text
    assert "Evidence" in response.json()["detail"]
    assert app.state.storage.list_memories(repo_id="repo-a") == []


@pytest.mark.asyncio
async def test_export_exceeds_http_list_limit_without_truncation(owner_client):
    data = {
        "version": "3.0",
        "memories": {"episodic": [
            {"id": f"memory-{index}", "content": f"Observation {index}", "repo_id": "repo-a"}
            for index in range(1001)
        ]},
    }
    app.state.storage.import_graph(data, default_repo_id="repo-a")
    response = await owner_client.get("/repos/repo-a/export")
    assert response.status_code == 200, response.text
    assert len(response.json()["memories"]["episodic"]) == 1001


@pytest.mark.asyncio
async def test_attestation_absence_and_missing_memory_are_404(owner_client):
    memory_id = app.state.storage.store_memory("No attestation", repo_id="repo-a")
    for item_id in (memory_id, "missing"):
        response = await owner_client.get(f"/memories/{item_id}/attestation")
        assert response.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("supported", [False, True])
async def test_attestation_handles_backends_without_peek(owner_client, supported):
    attestation = {"belief_id": "belief", "envelope": "signed-envelope"}
    backend = SimpleNamespace(
        get_memory=lambda _id: {"id": "belief", "repo_id": "repo-a"},
    )
    if supported:
        backend.get_authority_attestation = lambda _id: attestation
    previous = app.state.storage
    app.state.storage = backend
    try:
        response = await owner_client.get("/memories/belief/attestation?repo_id=repo-a")
    finally:
        app.state.storage = previous
    assert response.status_code == (200 if supported else 501), response.text
    if supported:
        assert response.json() == attestation


@pytest.mark.asyncio
async def test_import_refuses_archived_repository(owner_client):
    app.state.storage.store_repository({"id": "repo-a", "name": "Archived", "status": "archived"})
    response = await owner_client.post(
        "/repos/repo-a/import", json=graph_document(), headers=OWNER_HEADERS,
    )
    assert response.status_code == 409
    assert app.state.storage.peek_memory("portable-memory") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("data", [
    {"memories": {"episodic": [{"content": 123}]}},
    {"version": "future", "memories": {}},
    {"version": "3.0", "evidence": ["malformed"]},
    {"version": "3.0", "memories": {"raw": "malformed"}},
])
async def test_import_validation_returns_422_without_writes(owner_client, data):
    response = await owner_client.post("/repos/repo-a/import", json=data, headers=OWNER_HEADERS)
    assert response.status_code == 422, response.text
    assert app.state.storage.list_memories(repo_id="repo-a") == []


@pytest.mark.asyncio
async def test_import_legacy_document_uses_existing_quarantine_policy(owner_client):
    data = graph_document("repo-a")
    data["version"] = "1.0"
    response = await owner_client.post("/repos/repo-a/import", json=data, headers=OWNER_HEADERS)
    assert response.status_code == 200, response.text
    imported = app.state.storage.list_memories(repo_id="repo-a")[0]
    assert imported["source"] == "external"
    assert imported["metadata"]["written_by"] == "original-writer"


@pytest.mark.asyncio
async def test_attestation_is_scoped_without_access_side_effects(owner_client, monkeypatch):
    storage = app.state.storage
    memory_id = storage.store_memory("Observation", repo_id="repo-a", auto_link=False)
    before = storage.peek_memory(memory_id)
    attestation = {"belief_id": memory_id, "envelope": "signed-envelope"}
    monkeypatch.setattr(
        storage, "get_authority_attestation", lambda _id: copy.deepcopy(attestation)
    )
    response = await owner_client.get(f"/memories/{memory_id}/attestation?repo_id=repo-a")
    assert response.status_code == 200, response.text
    assert response.json() == attestation
    response = await owner_client.get(f"/memories/{memory_id}/attestation?repo_id=repo-b")
    assert response.status_code == 404
    assert storage.peek_memory(memory_id) == before


@pytest.mark.asyncio
async def test_existing_reindex_route_is_guarded_and_dry_run_is_read_only(owner_client):
    storage = app.state.storage
    memory_id = storage.store_memory("Indexed observation", repo_id="repo-a", auto_link=False)
    before = storage.peek_memory(memory_id)
    payload = {"repo_id": "repo-a", "dry_run": True}
    response = await owner_client.post("/diagnostics/embedding-index/reindex", json=payload)
    assert response.status_code == 403
    response = await owner_client.post(
        "/diagnostics/embedding-index/reindex", json=payload, headers=OWNER_HEADERS,
    )
    assert response.status_code == 200, response.text
    assert response.json()["matched_memories"] == 1
    assert response.json()["reindexed_memories"] == 0
    assert response.json()["dry_run"] is True
    assert storage.peek_memory(memory_id) == before


@pytest.mark.asyncio
async def test_portability_capabilities_follow_backend_and_unsupported_routes_refuse(
    owner_client, monkeypatch,
):
    capabilities = StorageCapabilities()
    monkeypatch.setattr(app.state.storage, "get_capabilities", lambda: capabilities)
    response = await owner_client.get("/diagnostics/capabilities")
    assert response.json() == capabilities.to_dict()
    response = await owner_client.get("/repos/repo-a/export")
    assert response.status_code == 501
    response = await owner_client.post(
        "/repos/repo-a/import", json=graph_document(), headers=OWNER_HEADERS,
    )
    assert response.status_code == 501
