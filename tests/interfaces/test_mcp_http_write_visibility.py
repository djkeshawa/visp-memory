"""HTTP writes remain visible to their authenticated team in new project scopes."""

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


@pytest.fixture
def new_project_server(tmp_path, monkeypatch):
    monkeypatch.setenv("VISP_MEMORY_MCP_PROFILE", "full")
    config = MemoryConfig(repo_id="new-project")
    config.storage.data_dir = tmp_path
    config.embedding.provider = "noop"
    with Memory(config=config) as memory:
        def authenticate(token):
            if token not in ("alpha", "beta"):
                return None
            return {
                "user_id": token, "username": token, "team_id": token,
                "scopes": ["memory:read", "memory:write"], "repo_ids": ["new-project"],
            }

        with patch("visp_memory.interfaces.mcp.Memory", return_value=memory):
            server = create_mcp_server()
        app = build_http_app(server, auth_store=SimpleNamespace(authenticate_token=authenticate))
        yield app, memory


async def call_tool(client, token, name, arguments):
    response = await client.post(
        "/mcp",
        headers={
            "accept": "application/json, text/event-stream",
            "authorization": f"Bearer {token}",
        },
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": name, "arguments": {"repo_id": "new-project", **arguments},
        }},
    )
    assert response.status_code == 200
    result = response.json()["result"]
    assert not result.get("isError"), result
    return result["content"][0]["text"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("memory_record", {"event": "Useful alpha event"}),
        ("memory_learn", {"knowledge": "Useful alpha knowledge"}),
    ],
)
async def test_mcp_new_project_writes_are_visible_only_to_the_creating_team(
    new_project_server, tool, arguments
):
    app, memory = new_project_server
    async with app.lifespan(), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:
        await call_tool(client, "alpha", tool, arguments)
        own_stats = json.loads(await call_tool(client, "alpha", "memory_stats", {}))
        other_stats = json.loads(await call_tool(client, "beta", "memory_stats", {}))

    assert own_stats["total_memories"] == 1
    assert other_stats["total_memories"] == 0
    record = memory._storage.list_memories(repo_id="new-project")[0]
    assert record["metadata"]["author_id"] == "alpha"
    assert record["metadata"]["team_id"] == "alpha"
    assert record["metadata"]["write_channel"] == "mcp"
    own_view = memory_for_principal(
        memory, UserContext(user_id="alpha", username="alpha", team_id="alpha"), "new-project"
    )
    assert own_view._storage.peek_memory(record["id"])["id"] == record["id"]
    other_view = memory_for_principal(
        memory, UserContext(user_id="beta", username="beta", team_id="beta"), "new-project"
    )
    assert record["evidence_ids"]
    for evidence_id in record["evidence_ids"]:
        assert own_view._storage.get_evidence(evidence_id)["metadata"]["team_id"] == "alpha"
        assert other_view._storage.get_evidence(evidence_id) is None
