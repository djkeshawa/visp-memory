import json
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest

from visp_memory import Memory, MemoryConfig

pytest.importorskip("mcp")

from visp_memory.interfaces.mcp import create_mcp_server  # noqa: E402
from visp_memory.interfaces.mcp_http import build_http_app  # noqa: E402
from visp_memory.interfaces.mcp_memory_view import memory_for_principal  # noqa: E402
from visp_memory.server.auth import UserContext  # noqa: E402

HEADERS = {
    "accept": "application/json, text/event-stream",
    "authorization": "Bearer audit-token",
}


def test_request_view_keeps_shared_memory_and_other_principals_unchanged(tmp_path):
    config = MemoryConfig(repo_id="server-default")
    config.storage.data_dir = tmp_path
    config.embedding.provider = "noop"
    with Memory(config=config) as memory:
        storage = memory._storage
        memory_ids = [
            storage.store_memory(
                team, repo_id="project", metadata={"team_id": team}, auto_link=False
            ) for team in ("alpha", "beta")
        ]
        views = [
            memory_for_principal(
                memory, UserContext(user_id=team, username=team, team_id=team), "project"
            ) for team in ("alpha", "beta")
        ]

        for view, expected in zip(views, memory_ids):
            assert [row["id"] for row in view._storage.list_memories()] == [expected]
            assert view.intent.storage is view._storage
            assert view.semantic.storage is view._storage
            view.close()

        assert memory.config.repo_id == "server-default"
        assert memory._storage is storage
        assert memory.semantic.storage is storage
        assert {row["id"] for row in storage.list_memories(repo_id="project")} == set(memory_ids)


@pytest.fixture
def team_server(tmp_path, monkeypatch):
    monkeypatch.setenv("VISP_MEMORY_MCP_PROFILE", "full")
    config = MemoryConfig(repo_id="team-audit")
    config.storage.data_dir = tmp_path
    config.embedding.provider = "noop"
    memory = Memory(config=config)
    storage = memory._storage
    storage.store_repository({"id": "team-audit", "name": "Team", "team_id": "alpha"})
    visible = storage.store_memory(
        "Visible login guidance", repo_id="team-audit", tags=["provenance:authored"],
        metadata={"team_id": "alpha"}, auto_link=False,
    )
    evidence = storage.store_evidence("Private beta login rule", repo_id="team-audit")
    hidden = storage.store_memory(
        "Private beta login rule in src/login.py", layer="semantic", category="negative",
        repo_id="team-audit", tags=["provenance:authored", "warning"],
        metadata={"team_id": "beta", "files": ["src/login.py"]},
        evidence_ids=[evidence], auto_link=False,
    )
    storage.add_relationship(visible, hidden, "supports")
    storage.set_intent(
        "Private beta login intent", repo_id="team-audit",
        context={"team_id": "beta", "constraints": ["Private beta constraint"]},
    )
    principal = {
        "user_id": "alice", "username": "alice", "team_id": "alpha",
        "scopes": ["memory:read", "memory:write"], "repo_ids": ["team-audit"],
    }
    auth_store = SimpleNamespace(
        authenticate_token=lambda token: principal if token == "audit-token" else None
    )
    with patch("visp_memory.interfaces.mcp.Memory", return_value=memory):
        server = create_mcp_server()
    app = build_http_app(server, auth_store=auth_store)
    yield app, memory, visible, hidden
    memory.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("memory_recall", {"query": "Private beta login"}),
        ("memory_context", {}),
        ("memory_prepare_task", {"task": "Review login", "format": "json"}),
        ("memory_trace", {"query": "Private beta login"}),
        ("memory_file_context", {"file_path": "src/login.py"}),
        ("memory_list_intents", {}),
    ],
)
async def test_mcp_http_tools_filter_records_before_using_them(team_server, tool, arguments):
    app, _, _, hidden = team_server
    async with app.lifespan(), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/mcp", headers=HEADERS,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                "name": tool, "arguments": {"repo_id": "team-audit", **arguments}
            }},
        )

    assert response.status_code == 200
    result = response.json()["result"]["content"][0]["text"]
    assert "Private beta login rule in src/login.py" not in result
    assert "Private beta login intent" not in result
    assert "Private beta constraint" not in result
    assert hidden not in result
    assert "authorization_denied" not in result


@pytest.mark.asyncio
async def test_refused_hidden_id_does_not_increase_the_memory_access_count(team_server):
    app, memory, _, hidden = team_server
    before = memory._storage.peek_memory(hidden)["access_count"]
    async with app.lifespan(), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/mcp", headers=HEADERS,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                "name": "memory_neighbors",
                "arguments": {"repo_id": "team-audit", "memory_id": hidden},
            }},
        )

    assert "authorization_denied" in response.text
    assert memory._storage.peek_memory(hidden)["access_count"] == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool", "arguments", "description"),
    [
        ("memory_goal", {"goal": "Deliver login"}, "Deliver login"),
        ("memory_working_on", {"task": "Deliver login"}, "WORKING ON: Deliver login"),
    ],
)
async def test_mcp_intent_creator_can_connect_a_workflow_report(
    team_server, tool, arguments, description
):
    app, memory, _, _ = team_server
    async with app.lifespan(), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        created = await client.post(
            "/mcp", headers=HEADERS,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                "name": tool, "arguments": {"repo_id": "team-audit", **arguments},
            }},
        )
        assert "authorization_denied" not in created.text
        intent = next(
            row for row in memory._storage.get_active_intents(repo_id="team-audit")
            if row["description"] == description
        )
        reported = await client.post(
            "/mcp", headers=HEADERS,
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
                "name": "memory_update_intent", "arguments": {
                    "repo_id": "team-audit", "intent_id": intent["id"],
                    "workflow_report": {
                        "source": "assistant", "task_id": "task-1", "event_id": "event-1",
                        "revision": 1, "status": "completed", "summary": "Login delivered",
                        "evidence": [{"description": "Acceptance tests passed"}],
                    },
                },
            }},
        )

    text = reported.json()["result"]["content"][0]["text"]
    assert json.loads(text)["applied"]
    assert intent["context"]["author_id"] == "alice"
    assert intent["context"]["team_id"] == "alpha"


@pytest.mark.asyncio
async def test_mcp_intent_write_keeps_the_exact_authorized_repository(team_server, monkeypatch):
    app, memory, _, _ = team_server
    repo_id = "team-audit "
    memory._storage.store_repository({"id": repo_id, "name": "Exact", "team_id": "alpha"})
    monkeypatch.setattr(app._auth_store, "authenticate_token", lambda token: {
        "user_id": "alice", "username": "alice", "team_id": "alpha",
        "scopes": ["memory:write"], "repo_ids": [repo_id],
    })
    async with app.lifespan(), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        created = await client.post(
            "/mcp", headers=HEADERS,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                "name": "memory_goal", "arguments": {"repo_id": repo_id, "goal": "Exact scope"},
            }},
        )

    assert created.status_code == 200
    result = created.json()["result"]
    assert not result.get("isError")
    intent = next(
        row for row in memory._storage.get_active_intents() if row["description"] == "Exact scope"
    )
    assert intent["repo_id"] == repo_id


@pytest.mark.asyncio
@pytest.mark.parametrize("resource", ["context", "warnings", "goals"])
async def test_mcp_http_resources_apply_record_visibility(team_server, resource):
    app, _, _, hidden = team_server
    async with app.lifespan(), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/mcp", headers=HEADERS,
            json={"jsonrpc": "2.0", "id": 1, "method": "resources/read", "params": {
                "uri": f"memory://repo/team-audit/{resource}"
            }},
        )

    assert response.status_code == 200
    payload = response.json()
    assert "error" not in payload
    result = json.dumps(payload["result"])
    assert "Private beta" not in result
    assert hidden not in result
