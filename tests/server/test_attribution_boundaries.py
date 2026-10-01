"""Request attribution cannot inherit server labels or be replaced by payloads."""

import pytest

from tests.server.test_attribution_headers import AUTH_HEADERS, WRITER_HEADERS, WRITTEN_BY
from visp_memory.server.app import app


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "route,body,field",
    [
        ("/memories", {"content": "anonymous memory"}, "metadata"),
        ("/evidence", {"content": "anonymous evidence"}, "metadata"),
        ("/intents", {"description": "anonymous intent"}, "context"),
    ],
)
@pytest.mark.parametrize(
    "labels,expected",
    [
        ({}, None),
        ({"X-Visp-Agent": "bad label", "X-Visp-Session": "x" * 65}, None),
        ({"X-Visp-Session": "request-session"}, {"session": "request-session"}),
        ({"X-Visp-Client": "dashboard"}, {"client": "dashboard"}),
    ],
)
async def test_rest_never_inherits_server_writer(
    client, monkeypatch, route, body, field, labels, expected
):
    monkeypatch.setenv("VISP_MEMORY_AGENT", "server-agent")
    monkeypatch.setenv("VISP_MEMORY_SESSION", "server-session")
    response = await client.post(
        route, json={**body, "repo_id": "repo-a"}, headers={**AUTH_HEADERS, **labels}
    )
    assert response.status_code == 200, response.text
    assert response.json()[field].get("written_by") == expected
    audit = await client.get("/platform/audit-log?repo_id=repo-a", headers=AUTH_HEADERS)
    assert audit.status_code == 200, audit.text
    for event in audit.json():
        assert event["metadata"].get("written_by") == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("headers,expected", [(AUTH_HEADERS, None), (WRITER_HEADERS, WRITTEN_BY)])
async def test_intent_create_drops_body_writer(client, headers, expected):
    response = await client.post(
        "/intents",
        headers=headers,
        json={
            "description": "unforgeable intent writer",
            "repo_id": "repo-a",
            "context": {"written_by": {"session": "forged"}, "safe": "kept"},
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["context"].get("written_by") == expected
    listed = await client.get("/intents?repo_id=repo-a", headers=AUTH_HEADERS)
    assert listed.status_code == 200, listed.text
    assert listed.json()[0]["context"].get("written_by") == expected
    assert listed.json()[0]["context"]["safe"] == "kept"


@pytest.mark.asyncio
@pytest.mark.parametrize("admin", [False, True])
@pytest.mark.parametrize("original_writer", [None, WRITTEN_BY])
async def test_intent_patch_preserves_original_writer(client, admin, original_writer):
    from visp_memory.server.auth import UserContext
    from visp_memory.server.routers import intents

    # Exercise ownership pinning as well as the admin branch.
    user = UserContext(user_id="owner", username="owner", is_admin=admin, team_id="original-team")

    async def current_user():
        return user

    app.dependency_overrides[intents.get_current_user] = current_user
    try:
        context = {"author_id": "owner", "team_id": "original-team"}
        if original_writer:
            context["written_by"] = original_writer
        intent_id = app.state.storage.set_intent(
            "patch attribution", repo_id="repo-a", context=context
        )
        response = await client.patch(
            f"/intents/{intent_id}",
            headers=WRITER_HEADERS,
            json={
                "context": {"written_by": {"session": "forged"}, "safe": "updated"},
            },
        )
        assert response.status_code == 200, response.text
        result = response.json()["context"]
        assert result.get("written_by") == original_writer
        assert result["safe"] == "updated"
        if not admin:
            assert result["author_id"] == "owner"
            assert result["team_id"] == "original-team"
    finally:
        app.dependency_overrides.pop(intents.get_current_user, None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "route,body",
    [
        ("/repos", {"name": "Attribution repo", "id": "repo-a"}),
        ("/teams", {"name": "Attribution team", "id": "team-a"}),
        ("/teams/users", {"username": "attribution-user", "id": "user-a"}),
    ],
)
async def test_other_rest_metadata_drops_body_writer(client, route, body):
    response = await client.post(
        route,
        headers=AUTH_HEADERS,
        json={
            **body,
            "metadata": {"written_by": {"session": "forged"}, "safe": "kept"},
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["metadata"] == {"safe": "kept"}


@pytest.mark.asyncio
async def test_recall_event_metadata_drops_body_writer(client):
    memory = await client.post(
        "/memories",
        headers=AUTH_HEADERS,
        json={
            "content": "feedback attribution",
            "repo_id": "repo-a",
        },
    )
    response = await client.post(
        "/recall-events",
        headers=AUTH_HEADERS,
        json={
            "memory_id": memory.json()["id"],
            "repo_id": "repo-a",
            "event_type": "used",
            "metadata": {"written_by": {"session": "forged"}, "safe": "kept"},
        },
    )
    assert response.status_code == 200, response.text
    report = await client.get("/recall-events/utility?repo_id=repo-a", headers=AUTH_HEADERS)
    assert report.status_code == 200, report.text
    assert report.json()["events"][0]["metadata"] == {"safe": "kept"}


@pytest.mark.asyncio
async def test_relationship_schema_drops_body_attribution(client):
    ids = []
    for content in ("source attribution", "target attribution"):
        memory = await client.post(
            "/memories",
            headers=AUTH_HEADERS,
            json={
                "content": content,
                "repo_id": "repo-a",
            },
        )
        assert memory.status_code == 200, memory.text
        ids.append(memory.json()["id"])
    response = await client.post(
        "/relationships",
        headers=AUTH_HEADERS,
        json={
            "source_id": ids[0],
            "target_id": ids[1],
            "relationship": "supports",
            "metadata": {"written_by": {"session": "forged"}},
            "evidence": {
                "reason": "kept",
                "written_by": {"session": "forged"},
                "metadata": {"written_by": {"session": "forged"}},
            },
        },
    )
    assert response.status_code == 200, response.text
    stored = app.state.storage.get_all_relationships(repo_id="repo-a")
    assert "metadata" not in stored[0]
    assert "written_by" not in stored[0]["evidence"]
    assert "metadata" not in stored[0]["evidence"]
    assert stored[0]["evidence"]["reason"] == "kept"
