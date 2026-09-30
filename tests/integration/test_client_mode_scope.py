"""Client-mode calls that name no project still work against a shared server.

A shared server answers every unscoped request with 400 "repo_id is required".
These paths pass no repo_id and relied on the client to fill in its configured
project, which five RemoteStorage methods never did.
"""

import json

import pytest

pytest_plugins = ["tests.integration.shared_server"]


def _seed(memory):
    memory.record("Deployed the API to staging")
    memory.warn("migrations", "Never edit an applied migration")
    memory.goal("Ship project A")


def test_unscoped_memory_operations_use_the_configured_project(client_memory):
    project_a = client_memory("proj-a")
    project_b = client_memory("proj-b")
    _seed(project_a)
    project_b.goal("Ship project B")

    storage = project_a._storage
    assert [i["description"] for i in storage.get_active_intents()] == ["Ship project A"]
    assert all(m["repo_id"] == "proj-a" for m in storage.list_memories())
    assert storage.search_memories("staging")
    assert storage.get_all_relationships() == []
    assert storage.get_stats()

    warnings = project_a.semantic.get_warnings()
    assert any("Never edit an applied migration" in w["content"] for w in warnings)
    assert project_a.compress() == []
    assert project_a.decay() >= 0
    assert project_a.intent.clear_all() == 1
    assert [i["description"] for i in project_b._storage.get_active_intents()] == [
        "Ship project B"
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool,arguments,expected",
    [
        ("memory_list_intents", {}, "Ship project A"),
        ("memory_list_warnings", {}, "Never edit an applied migration"),
        ("memory_compress", {}, "Compression complete"),
        ("memory_decay", {}, "Decay applied"),
        ("memory_clear_goals", {"confirm": True}, "Recorded 1 goal outcomes"),
    ],
)
async def test_mcp_tools_succeed_in_client_mode_on_a_shared_server(
    client_memory, monkeypatch, tool, arguments, expected
):
    from mcp.types import CallToolRequest, CallToolRequestParams

    from visp_memory.interfaces import mcp as mcp_module

    monkeypatch.setenv("VISP_MEMORY_MCP_PROFILE", "full")
    memory = client_memory("proj-a")
    _seed(memory)
    monkeypatch.setattr(mcp_module, "Memory", lambda *a, **k: memory)
    server = mcp_module.create_mcp_server()

    result = await server.request_handlers[CallToolRequest](
        CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(name=tool, arguments=arguments),
        )
    )

    text = result.root.content[0].text
    assert result.root.isError is False
    assert expected in text, text
    with pytest.raises(json.JSONDecodeError):
        json.loads(text)
