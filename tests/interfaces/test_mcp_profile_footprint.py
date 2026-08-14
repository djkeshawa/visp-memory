"""The advertised tool-schema footprint is measured, not asserted.

README and ARCHITECTURE both quote a saving for the `core` profile. That number
was written by hand and nothing checked it: the README said "roughly 40%
(~1,250 fewer tokens)" while ARCHITECTURE said "roughly halving", and neither
figure came from the tool definitions the server actually advertises. Two
documents disagreeing about one measurable quantity is the tell that neither
measured it.

This test measures it. It serialises exactly what an MCP client receives -- the
name, description and input schema of every advertised tool -- and pins the
reduction the `core` profile buys. The documented percentage is the floor
asserted here, so a tool added to `core` that erodes the saving fails this test
before it can make the README wrong.

Bytes, not tokens, are the asserted unit on purpose. Tokenisation is
model-specific and no tokeniser is a dependency of this package; a token count
would be a second unverifiable number standing on the first.
"""

import json
from unittest import mock

import pytest

from visp_memory.config import MemoryConfig
from visp_memory.core.memory import Memory

# Documented in README.md ("Tool Profiles") and
# docs/development/ARCHITECTURE.md. Keep the three in agreement.
#
# The measured figure at the time of writing is 39.5%, which the docs round to
# "about 39%" -- deliberately down, not up: 39.5 presented as "roughly 40%" was
# the original claim, and a number rounded in the direction that flatters the
# product is how an unbacked figure starts. The band below is wide enough that
# editing a tool description does not fail CI, and tight enough that adding a
# tool to `core` or gutting `full` does.
DOCUMENTED_CORE_REDUCTION_FLOOR = 0.35
DOCUMENTED_CORE_REDUCTION_CEILING = 0.45
DOCUMENTED_CORE_TOOL_COUNT = 17
DOCUMENTED_READONLY_TOOL_COUNT = 6
DOCUMENTED_FULL_TOOL_COUNT = 36


async def _advertised_payload_bytes(tmp_path, monkeypatch, profile: str) -> tuple[int, int]:
    """Return (tool count, serialized byte size) of what `profile` advertises."""
    from mcp.types import ListToolsRequest

    from visp_memory.interfaces.mcp import create_mcp_server

    config = MemoryConfig(repo_id="footprint-repo")
    config.storage.data_dir = tmp_path / profile
    config.embedding.provider = "noop"
    memory = Memory(config=config)
    monkeypatch.setenv("VISP_MEMORY_MCP_PROFILE", profile)
    with mock.patch("visp_memory.interfaces.mcp.Memory", return_value=memory):
        server = create_mcp_server()
    response = await server.request_handlers[ListToolsRequest](ListToolsRequest())
    tools = response.root.tools
    # What crosses the wire into the assistant's context: name, description,
    # input schema. Nothing else about a Tool costs the client anything.
    payload = json.dumps(
        [
            {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.inputSchema,
            }
            for tool in sorted(tools, key=lambda t: t.name)
        ],
        ensure_ascii=False,
        sort_keys=True,
    )
    return len(tools), len(payload.encode("utf-8"))


@pytest.fixture(autouse=True)
def _require_mcp():
    from visp_memory.interfaces.mcp import MCP_AVAILABLE

    if not MCP_AVAILABLE:
        pytest.skip("MCP not installed")


class TestAdvertisedFootprint:
    @pytest.mark.asyncio
    async def test_documented_tool_counts_are_the_real_ones(self, tmp_path, monkeypatch):
        """The counts printed in the docs are the counts the server advertises.

        The README claimed 19 core tools for as long as there were 17. A reader
        sizing their context budget against that number was told to expect two
        tools that do not exist.
        """
        core, _ = await _advertised_payload_bytes(tmp_path, monkeypatch, "core")
        readonly, _ = await _advertised_payload_bytes(tmp_path, monkeypatch, "readonly")
        full, _ = await _advertised_payload_bytes(tmp_path, monkeypatch, "full")

        assert core == DOCUMENTED_CORE_TOOL_COUNT
        assert readonly == DOCUMENTED_READONLY_TOOL_COUNT
        assert full == DOCUMENTED_FULL_TOOL_COUNT

    @pytest.mark.asyncio
    async def test_core_profile_reduction_matches_the_documented_band(
        self, tmp_path, monkeypatch
    ):
        """`core` cuts the advertised schema payload by the documented share."""
        _, core_bytes = await _advertised_payload_bytes(tmp_path, monkeypatch, "core")
        _, full_bytes = await _advertised_payload_bytes(tmp_path, monkeypatch, "full")

        assert full_bytes > 0
        reduction = 1.0 - (core_bytes / full_bytes)
        assert (
            DOCUMENTED_CORE_REDUCTION_FLOOR <= reduction <= DOCUMENTED_CORE_REDUCTION_CEILING
        ), (
            f"core advertises {core_bytes} bytes against full's {full_bytes}: "
            f"{reduction:.1%} reduction, outside the documented band "
            f"{DOCUMENTED_CORE_REDUCTION_FLOOR:.0%}-"
            f"{DOCUMENTED_CORE_REDUCTION_CEILING:.0%}. "
            "Either the profiles moved or the documented figure is now a promise "
            "the code does not keep. Update both, or neither."
        )

    @pytest.mark.asyncio
    async def test_readonly_is_the_smallest_surface(self, tmp_path, monkeypatch):
        """Ordering is the invariant a reader relies on when choosing a profile."""
        _, readonly_bytes = await _advertised_payload_bytes(tmp_path, monkeypatch, "readonly")
        _, core_bytes = await _advertised_payload_bytes(tmp_path, monkeypatch, "core")
        _, full_bytes = await _advertised_payload_bytes(tmp_path, monkeypatch, "full")

        assert readonly_bytes < core_bytes < full_bytes
