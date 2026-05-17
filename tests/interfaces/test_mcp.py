import tempfile
from pathlib import Path
from unittest import mock

import pytest

from llm_memory import Memory, MemoryConfig


class TestMCPServer:
    """Tests for MCP server."""

    def test_mcp_server_creates(self):
        """MCP server can be created."""
        try:
            from llm_memory.interfaces.mcp import MCP_AVAILABLE, create_mcp_server

            if not MCP_AVAILABLE:
                pytest.skip("MCP not installed")

            with tempfile.TemporaryDirectory() as tmpdir:
                config = MemoryConfig()
                config.storage.data_dir = Path(tmpdir)
                config.embedding.provider = "noop"
                memory = Memory(config=config)

                with mock.patch("llm_memory.interfaces.mcp.Memory", return_value=memory):
                    server = create_mcp_server()

            assert server is not None
            assert server.name == "llm-memory"
        except ImportError:
            pytest.skip("MCP not installed")

    @pytest.mark.asyncio
    async def test_mcp_handle_tool(self):
        """MCP tool handler works."""
        try:
            from llm_memory.interfaces.mcp import MCP_AVAILABLE, handle_tool

            if not MCP_AVAILABLE:
                pytest.skip("MCP not installed")

            # Create a temp memory for testing
            import tempfile

            with tempfile.TemporaryDirectory() as tmpdir:
                config = MemoryConfig()
                config.storage.data_dir = Path(tmpdir)
                config.embedding.provider = "noop"
                memory = Memory(config=config)

                # Test memory_stats tool
                result = await handle_tool("memory_stats", {}, memory)
                assert "total_memories" in result
        except ImportError:
            pytest.skip("MCP not installed")

    @pytest.mark.asyncio
    async def test_mcp_handle_record(self):
        """MCP record tool works."""
        try:
            from llm_memory.interfaces.mcp import MCP_AVAILABLE, handle_tool

            if not MCP_AVAILABLE:
                pytest.skip("MCP not installed")

            import tempfile

            with tempfile.TemporaryDirectory() as tmpdir:
                config = MemoryConfig()
                config.storage.data_dir = Path(tmpdir)
                config.embedding.provider = "noop"
                memory = Memory(config=config)

                result = await handle_tool(
                    "memory_record", {"event": "Test event", "category": "note"}, memory
                )
                assert "Recorded" in result
        except ImportError:
            pytest.skip("MCP not installed")

    @pytest.mark.asyncio
    async def test_mcp_workflow_before_and_after_work(self):
        """Codex workflow tools recall before edits and record after work."""
        try:
            from llm_memory.interfaces.mcp import MCP_AVAILABLE, handle_tool

            if not MCP_AVAILABLE:
                pytest.skip("MCP not installed")

            with tempfile.TemporaryDirectory() as tmpdir:
                config = MemoryConfig()
                config.storage.data_dir = Path(tmpdir)
                config.embedding.provider = "noop"
                memory = Memory(config=config)
                memory.warn("src/server.py", "Check auth before changing routes")

                before = await handle_tool(
                    "memory_before_change",
                    {"task": "change server route", "files": ["src/server.py"]},
                    memory,
                )
                assert "Check auth" in before

                after = await handle_tool(
                    "memory_after_work",
                    {
                        "summary": "Fixed Docker server embedding fallback",
                        "bugs_fixed": ["Server no longer triggers Chroma default model download"],
                        "warnings": [
                            {
                                "area": "Dockerfile",
                                "warning": "Keep local embeddings optional to avoid huge images",
                            }
                        ],
                    },
                    memory,
                )
                assert "Recorded summary" in after
                assert "Recorded bug fix" in after
                assert "Recorded warning" in after
        except ImportError:
            pytest.skip("MCP not installed")
