"""P10-US-07: MCP profiles are enforced at dispatch, not only in advertisement.

Before this change the profile filter ran in list_tools while call_tool
dispatched by name with no profile check — the restriction only rearranged
the menu. These tests pin the new behavior: a tool outside the active
profile is a structured refusal, and the read-only profile permits no
writing tool.
"""

import json

import pytest

from visp_memory.interfaces.mcp import (
    CORE_TOOL_NAMES,
    READONLY_TOOL_NAMES,
    VALID_MCP_PROFILES,
    _profile_tool_names,
    _resolve_tool_profile,
)


class TestProfileNames:
    def test_readonly_profile_exists(self):
        assert "readonly" in VALID_MCP_PROFILES

    def test_readonly_subset_contains_no_writing_tool(self):
        # The read-only surface is retrieval and context only.
        writing = {
            "memory_record",
            "memory_decision",
            "memory_learn",
            "memory_warn",
            "memory_goal",
            "memory_working_on",
            "memory_done",
            "memory_session_start",
            "memory_before_change",
            "memory_after_work",
            "memory_feedback_log",
        }
        assert READONLY_TOOL_NAMES.isdisjoint(writing)
        assert READONLY_TOOL_NAMES <= CORE_TOOL_NAMES

    def test_profile_tool_names_resolves_each_profile(self):
        everything = frozenset({"a", "b"}) | CORE_TOOL_NAMES
        assert _profile_tool_names("full", everything) == everything
        assert _profile_tool_names("core", everything) == CORE_TOOL_NAMES
        assert _profile_tool_names("readonly", everything) == READONLY_TOOL_NAMES

    def test_env_resolution_falls_back_to_core(self, monkeypatch):
        monkeypatch.setenv("VISP_MEMORY_MCP_PROFILE", "nonsense")
        assert _resolve_tool_profile() == "core"
        monkeypatch.setenv("VISP_MEMORY_MCP_PROFILE", "readonly")
        assert _resolve_tool_profile() == "readonly"


@pytest.mark.asyncio
class TestDispatchEnforcement:
    async def _call(self, tmp_path, name: str, arguments: dict):
        from pathlib import Path

        from visp_memory.config import MemoryConfig
        from visp_memory.core.memory import Memory
        from visp_memory.interfaces import mcp as mcp_module
        from visp_memory.interfaces.mcp import create_mcp_server

        config = MemoryConfig()
        config.storage.data_dir = Path(tmp_path)
        config.embedding.provider = "noop"
        # create_mcp_server constructs Memory() from ambient config; pin it to
        # the isolated test store instead.
        original = mcp_module.Memory
        mcp_module.Memory = lambda *a, **k: Memory(config=config)  # type: ignore[assignment]
        try:
            server = create_mcp_server()
        finally:
            mcp_module.Memory = original  # type: ignore[assignment]

        from mcp.types import CallToolRequest, CallToolRequestParams

        handler = server.request_handlers[CallToolRequest]
        request = CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(name=name, arguments=arguments),
        )
        result = await handler(request)
        return result.root.content

    async def test_readonly_profile_refuses_a_write_tool_at_dispatch(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("VISP_MEMORY_MCP_PROFILE", "readonly")

        content = await self._call(tmp_path, "memory_record", {"event": "should never land"})
        payload = json.loads(content[0].text)
        assert payload["success"] is False
        assert payload["error"] == "tool_not_in_profile"
        assert payload["profile"] == "readonly"
        assert payload["tool"] == "memory_record"

    async def test_core_profile_refuses_a_non_core_tool_at_dispatch(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("VISP_MEMORY_MCP_PROFILE", "core")

        # A maintenance/admin tool outside CORE_TOOL_NAMES.
        content = await self._call(tmp_path, "memory_model_task", {"prompt": "x"})
        payload = json.loads(content[0].text)
        assert payload["success"] is False
        assert payload["error"] == "tool_not_in_profile"
