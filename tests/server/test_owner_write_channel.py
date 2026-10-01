"""Only server-validated owner proof promotes a REST write to assisted."""

import pytest

from tests.server.test_owner_maintenance import (
    LOOPBACK_PEER,
    OWNER_TOKEN,
    REMOTE_PEER,
    REPO_ID,
    WORKFLOW_REPORT,
)
from tests.server.test_owner_maintenance import owner_client as owner_client
from visp_memory.core.owner_token import OWNER_TOKEN_HEADER
from visp_memory.core.trust import Provenance, provenance_of
from visp_memory.server import app as server_app
from visp_memory.server.auth import SESSION_COOKIE_NAME, create_access_token


def _headers(principal):
    headers = {OWNER_TOKEN_HEADER: OWNER_TOKEN}
    config = server_app.config
    config.server.default_team = "alpha"
    if principal == "missing":
        return {}
    if principal == "wrong":
        return {OWNER_TOKEN_HEADER: "wrong-token"}
    if principal in {"pat", "session"}:
        account = server_app.app.state.auth_store.create_account(
            username="writer", password="a-long-test-password", team_id="alpha"
        )
        if principal == "pat":
            _, token = server_app.app.state.auth_store.create_token(
                user_id=account["id"], name="writer", scopes=["*"], repo_ids=[REPO_ID]
            )
            headers["Authorization"] = f"Bearer {token}"
        else:
            token, csrf = server_app.app.state.auth_store.create_session(account["id"])
            headers.update({"Cookie": f"{SESSION_COOKIE_NAME}={token}", "X-CSRF-Token": csrf})
    if principal == "api_key":
        config.server.api_keys = ["test-write-key"]
        headers["X-API-KEY"] = "test-write-key"
    if principal == "jwt":
        config.server.jwt_secret = "test-secret-for-owner-writes-32-chars"
        headers["Authorization"] = "Bearer " + create_access_token(
            {
                "sub": "writer",
                "team_id": "alpha",
                "owner_maintenance": True,
            }
        )
    return headers


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "principal", ["owner", "missing", "wrong", "pat", "session", "api_key", "jwt"]
)
@pytest.mark.parametrize(
    "kind",
    [
        "memory",
        "evidence",
        "revision",
        "revision_claimed",
        "complete",
        "close",
        "reopen",
        "outcomes",
    ],
)
async def test_rest_write_tier_comes_from_validated_owner_proof(owner_client, principal, kind):
    headers = _headers(principal)
    storage = server_app.app.state.storage
    metadata = {"team_id": "alpha", "author_id": "original-author", "write_channel": "mcp"}
    if kind == "memory":
        path = "/memories"
        payload = {
            "content": "A useful REST record",
            "repo_id": REPO_ID,
            "tags": ["topic", "provenance:authored", "provenance:derived"],
            "source": "authored",
            "metadata": {"write_channel": "mcp", "author_id": "fake"},
        }
    elif kind == "evidence":
        path = "/evidence"
        payload = {
            "content": "Observed the REST behavior",
            "repo_id": REPO_ID,
            "provenance": "authored",
            "metadata": {"write_channel": "mcp", "author_id": "fake"},
        }
    elif kind.startswith("revision"):
        old_evidence = storage.store_evidence("Old observation", repo_id=REPO_ID, metadata=metadata)
        new_evidence = storage.store_evidence("New observation", repo_id=REPO_ID, metadata=metadata)
        original = storage.store_memory(
            "Original belief",
            layer="semantic",
            category="fact",
            repo_id=REPO_ID,
            evidence_ids=[old_evidence],
            tags=["topic", "provenance:authored"],
            source="authored",
            metadata=metadata,
            auto_link=False,
        )
        path = f"/memories/{original}/revisions"
        # Omitted tags must preserve topic tags, but never inherit the old tier.
        payload = {
            "content": "Revised belief",
            "evidence_ids": [new_evidence],
            "metadata": {"write_channel": "mcp"},
        }
        if kind == "revision_claimed":
            payload["tags"] = ["topic", "provenance:authored", "provenance:assisted"]
    else:
        intent_id = storage.set_intent("Check REST writes", repo_id=REPO_ID, context=metadata)
        path = f"/intents/{intent_id}/{kind}"
        payload = {"outcome": "completed"} if kind == "outcomes" else None
    response = await owner_client.post(path, json=payload, headers=headers)
    assert response.status_code == 200, response.text
    tier = "assisted" if principal == "owner" else "external"
    channel = (
        "local_owner"
        if principal == "owner"
        else ("rest" if kind in {"complete", "close", "reopen", "outcomes"} else "http")
    )
    if kind in {"memory", "revision", "revision_claimed", "evidence"}:
        record = response.json()
        assert record["metadata"]["write_channel"] == channel
        assert record["metadata"]["author_id"] not in {"fake", "original-author"}
        if kind == "evidence":
            assert record["provenance"] == tier
        else:
            assert provenance_of(record) is Provenance(tier)
            assert record["source"] == tier
            assert record["tags"] == ["topic", f"provenance:{tier}"]
    else:
        intent = storage.get_active_intents(repo_id=REPO_ID, status="all")[0]
        entry = intent["context"]["outcome_history"][-1]
        assert entry["provenance"] == {"channel": channel, "source": tier, "tier": tier}
        assert intent["status"] == "active"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "owner_client", [(REMOTE_PEER, True), (LOOPBACK_PEER, False)], indirect=True
)
@pytest.mark.parametrize("kind", ["memory", "evidence"])
async def test_owner_token_without_local_owner_boundary_is_external(owner_client, kind):
    headers = _headers("owner")
    response = await owner_client.post(
        "/memories" if kind == "memory" else "/evidence",
        json={
            "content": "Boundary check",
            "repo_id": REPO_ID,
            "tags": ["provenance:assisted"],
            "provenance": "assisted",
            "metadata": {"owner_maintenance": True, "write_channel": "local_owner"},
        },
        headers={**headers, "X-Visp-Owner-Maintenance": "true"},
    )
    assert response.status_code == 200, response.text
    record = response.json()
    assert record["metadata"]["write_channel"] == "http"
    assert (
        record["provenance"] if kind == "evidence" else provenance_of(record).value
    ) == "external"


@pytest.mark.asyncio
async def test_owner_workflow_report_records_its_server_channel(owner_client):
    storage = server_app.app.state.storage
    intent_id = storage.set_intent("Track workflow", repo_id=REPO_ID)
    response = await owner_client.post(
        f"/intents/{intent_id}/workflow-status",
        json=WORKFLOW_REPORT,
        headers={OWNER_TOKEN_HEADER: OWNER_TOKEN},
    )
    assert response.status_code == 200, response.text
    intent = storage.get_active_intents(repo_id=REPO_ID, status="all")[0]
    assert intent["context"]["external_workflow"]["channel"] == "local_owner"
