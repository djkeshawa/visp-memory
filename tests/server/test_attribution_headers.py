"""REST attribution stays informational while crossing the HTTP boundary."""

import httpx
import pytest
from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware

from visp_memory.core.attribution import current_writer
from visp_memory.core.trust import Provenance, provenance_of
from visp_memory.server.attribution_middleware import AttributionMiddleware

AUTH_HEADERS = {"X-API-KEY": "test_key"}
WRITER_HEADERS = {
    **AUTH_HEADERS,
    "X-Visp-Agent": "codex",
    "X-Visp-Session": "session-1",
    "X-Visp-Client": "visp-sdk",
}
WRITTEN_BY = {
    "agent": "codex",
    "session": "session-1",
    "client": "visp-sdk",
}


@pytest.mark.asyncio
async def test_headers_stamp_memory_and_evidence_while_body_identity_is_ignored(
    client, monkeypatch
):
    monkeypatch.delenv("VISP_MEMORY_AGENT", raising=False)
    monkeypatch.delenv("VISP_MEMORY_SESSION", raising=False)
    forged = {"agent": "user", "session": "forged", "trusted": True}

    memory = await client.post(
        "/memories",
        json={
            "content": "Attributed REST memory",
            "repo_id": "repo-a",
            "metadata": {"written_by": forged, "safe": "kept"},
        },
        headers=WRITER_HEADERS,
    )
    evidence = await client.post(
        "/evidence",
        json={
            "content": "Attributed REST evidence",
            "repo_id": "repo-a",
            "metadata": {"written_by": forged, "safe": "kept"},
        },
        headers=WRITER_HEADERS,
    )

    assert memory.status_code == 200, memory.text
    assert evidence.status_code == 200, evidence.text
    assert memory.json()["metadata"]["written_by"] == WRITTEN_BY
    assert evidence.json()["metadata"]["written_by"] == WRITTEN_BY
    assert memory.json()["metadata"]["safe"] == "kept"
    assert evidence.json()["metadata"]["safe"] == "kept"

    body_only = await client.post(
        "/memories",
        json={
            "content": "Body attribution stays untrusted",
            "repo_id": "repo-a",
            "metadata": {"written_by": forged},
        },
        headers=AUTH_HEADERS,
    )
    assert body_only.status_code == 200, body_only.text
    assert "written_by" not in body_only.json()["metadata"]


@pytest.mark.asyncio
async def test_invalid_headers_are_dropped_and_labels_do_not_change_trust(client, monkeypatch):
    monkeypatch.delenv("VISP_MEMORY_AGENT", raising=False)
    monkeypatch.delenv("VISP_MEMORY_SESSION", raising=False)
    partially_invalid = {
        **AUTH_HEADERS,
        "X-Visp-Agent": "x" * 65,
        "X-Visp-Session": "bad label",
        "X-Visp-Client": "valid-client",
    }

    invalid = await client.post(
        "/memories",
        json={"content": "Invalid labels are dropped", "repo_id": "repo-a"},
        headers=partially_invalid,
    )
    assert invalid.status_code == 200, invalid.text
    assert invalid.json()["metadata"]["written_by"] == {"client": "valid-client"}

    labelled = await client.post(
        "/memories",
        json={
            "content": "Identity labels grant no trust",
            "repo_id": "repo-a",
            "source": "authored",
            "tags": ["provenance:authored"],
        },
        headers={
            **AUTH_HEADERS,
            "X-Visp-Agent": "verified",
            "X-Visp-Session": "user",
            "X-Visp-Client": "authored",
        },
    )

    assert labelled.status_code == 200, labelled.text
    record = labelled.json()
    assert record["metadata"]["written_by"] == {
        "agent": "verified",
        "session": "user",
        "client": "authored",
    }
    assert provenance_of(record) is Provenance.EXTERNAL
    assert record["tags"] == ["provenance:external"]
    assert record["source"] == "external"


@pytest.mark.asyncio
async def test_audit_events_include_request_writer_identity(client):
    memory = await client.post(
        "/memories",
        json={"content": "Audited attributed memory", "repo_id": "repo-a"},
        headers=WRITER_HEADERS,
    )
    intent = await client.post(
        "/intents",
        json={"description": "Audit attributed workflow", "repo_id": "repo-a"},
        headers=WRITER_HEADERS,
    )
    intent_id = intent.json()["id"]
    updated = await client.patch(
        f"/intents/{intent_id}",
        json={"priority": 2},
        headers=WRITER_HEADERS,
    )
    workflow = await client.post(
        f"/intents/{intent_id}/workflow-status",
        json={
            "source": "assistant",
            "task_id": "attribution",
            "event_id": "completed-1",
            "revision": 1,
            "status": "completed",
            "summary": "Attribution carried through the workflow",
            "evidence": [{"description": "Attribution checks passed"}],
        },
        headers=WRITER_HEADERS,
    )

    assert memory.status_code == 200, memory.text
    assert intent.status_code == 200, intent.text
    assert updated.status_code == 200, updated.text
    assert workflow.status_code == 200, workflow.text
    audit = await client.get(
        "/platform/audit-log?repo_id=repo-a", headers=AUTH_HEADERS
    )
    assert audit.status_code == 200, audit.text
    events = {
        (event["event_type"], event["target_id"]): event for event in audit.json()
    }
    expected = {
        ("memory.created", memory.json()["id"]),
        ("intent.updated", intent_id),
        ("intent.workflow_status_reported", intent_id),
    }
    assert expected <= events.keys()
    for key in expected:
        assert events[key]["metadata"]["written_by"] == WRITTEN_BY


@pytest.mark.asyncio
async def test_pure_asgi_binding_reaches_async_and_sync_endpoints(monkeypatch):
    monkeypatch.delenv("VISP_MEMORY_AGENT", raising=False)
    monkeypatch.delenv("VISP_MEMORY_SESSION", raising=False)
    test_app = FastAPI()
    test_app.add_middleware(AttributionMiddleware)

    def identity_payload():
        identity = current_writer()
        return identity.to_metadata() if identity else {}

    @test_app.get("/async")
    async def async_identity():
        return identity_payload()

    @test_app.get("/sync")
    def sync_identity():
        return identity_payload()

    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as request:
        async_response = await request.get("/async", headers=WRITER_HEADERS)
        sync_response = await request.get("/sync", headers=WRITER_HEADERS)
        unbound_response = await request.get("/async")

    assert not issubclass(AttributionMiddleware, BaseHTTPMiddleware)
    assert async_response.json() == WRITTEN_BY
    assert sync_response.json() == WRITTEN_BY
    assert unbound_response.json() == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("original_writer", [None, {"agent": "first-writer"}])
async def test_metadata_replacement_preserves_only_the_existing_writer(client, original_writer):
    from visp_memory.server.app import app

    metadata = {} if original_writer is None else {"written_by": original_writer}
    memory_id = app.state.storage.store_memory(
        "Metadata replacement", repo_id="repo-a", metadata=metadata,
    )
    response = await client.patch(
        f"/memories/{memory_id}",
        json={"metadata": {"written_by": {"agent": "forged"}, "safe": "updated"}},
        headers=WRITER_HEADERS,
    )
    assert response.status_code == 200, response.text
    result = app.state.storage.peek_memory(memory_id)["metadata"]
    assert result["safe"] == "updated"
    if original_writer is None:
        assert "written_by" not in result
    else:
        assert result["written_by"] == original_writer
