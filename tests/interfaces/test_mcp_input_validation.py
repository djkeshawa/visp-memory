"""Tool arguments are checked against the advertised input schema before dispatch.

mcp 1.x did this inside `@server.call_tool()`; the 2.x lowlevel server does not,
so the server does it. Without it out-of-range values are stored and a malformed
call reads as a server failure instead of a usage error.
"""

from unittest import mock

import pytest

from tests.mcp_client import call_tool
from visp_memory import Memory, MemoryConfig
from visp_memory.interfaces.mcp import create_mcp_server

pytest.importorskip("mcp")


@pytest.fixture
def server_and_memory(tmp_path, monkeypatch):
    config = MemoryConfig(repo_id="validation-repo")
    config.storage.data_dir = tmp_path
    config.embedding.provider = "noop"
    memory = Memory(config=config)
    monkeypatch.setenv("VISP_MEMORY_MCP_PROFILE", "full")
    with mock.patch("visp_memory.interfaces.mcp.Memory", return_value=memory):
        yield create_mcp_server(), memory
    memory.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool", "arguments", "reason"),
    [
        ("memory_warn", {"area": "db", "warning": "x", "severity": 5}, "maximum"),
        ("memory_goal", {"goal": "ship", "priority": 99}, "is not one of"),
        ("memory_warn", {"area": "db"}, "'warning' is a required property"),
    ],
)
async def test_invalid_arguments_are_refused_before_anything_is_written(
    server_and_memory, tool, arguments, reason
):
    server, memory = server_and_memory
    before = memory.stats()["total_memories"]

    result = await call_tool(server, tool, arguments)

    assert result.is_error is True
    assert result.content[0].text.startswith("Input validation error:")
    assert reason in result.content[0].text
    assert memory.stats()["total_memories"] == before


@pytest.mark.asyncio
async def test_valid_arguments_still_dispatch(server_and_memory):
    server, _memory = server_and_memory
    result = await call_tool(
        server, "memory_warn", {"area": "db", "warning": "Never edit migrations", "severity": 1}
    )
    assert result.is_error is False
