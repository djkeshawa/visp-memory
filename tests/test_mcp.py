import pytest
from pathlib import Path
from llm_memory import Memory, MemoryConfig

class TestMCPServer:
    """Tests for MCP server."""

    def test_mcp_server_creates(self):
        """MCP server can be created."""
        try:
            from llm_memory.interfaces.mcp import create_mcp_server, MCP_AVAILABLE
            if not MCP_AVAILABLE:
                pytest.skip("MCP not installed")

            server = create_mcp_server()
            assert server is not None
            assert server.name == "llm-memory"
        except ImportError:
            pytest.skip("MCP not installed")

    @pytest.mark.asyncio
    async def test_mcp_handle_tool(self):
        """MCP tool handler works."""
        try:
            from llm_memory.interfaces.mcp import handle_tool, MCP_AVAILABLE
            if not MCP_AVAILABLE:
                pytest.skip("MCP not installed")

            # Create a temp memory for testing
            import tempfile
            with tempfile.TemporaryDirectory() as tmpdir:
                config = MemoryConfig()
                config.storage.data_dir = Path(tmpdir)
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
            from llm_memory.interfaces.mcp import handle_tool, MCP_AVAILABLE
            if not MCP_AVAILABLE:
                pytest.skip("MCP not installed")

            import tempfile
            with tempfile.TemporaryDirectory() as tmpdir:
                config = MemoryConfig()
                config.storage.data_dir = Path(tmpdir)
                memory = Memory(config=config)

                result = await handle_tool(
                    "memory_record",
                    {"event": "Test event", "category": "note"},
                    memory
                )
                assert "Recorded" in result
        except ImportError:
            pytest.skip("MCP not installed")
