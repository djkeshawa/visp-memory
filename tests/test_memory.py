"""Basic tests for LLM Memory system."""

import tempfile
from pathlib import Path

import pytest

from llm_memory import Memory, MemoryConfig
from llm_memory.layers.episodic import EpisodeCategory
from llm_memory.layers.semantic import KnowledgeCategory


@pytest.fixture
def memory():
    """Create a memory instance with temp directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config = MemoryConfig()
        config.storage.data_dir = Path(tmpdir)
        yield Memory(config=config)


class TestEpisodicMemory:
    """Tests for episodic memory layer."""

    def test_record_event(self, memory):
        """Can record a basic event."""
        mem_id = memory.record("Fixed a bug", category="bug_fixed")
        assert mem_id is not None
        assert len(mem_id) == 16

    def test_record_decision(self, memory):
        """Can record a decision with reasoning."""
        mem_id = memory.decision(
            what="Use PostgreSQL",
            why="Need ACID compliance",
            alternatives=["MongoDB", "MySQL"]
        )
        assert mem_id is not None

        # Verify it was stored
        results = memory.recall("PostgreSQL")
        assert len(results) > 0
        assert "PostgreSQL" in results[0]["content"]

    def test_record_bug(self, memory):
        """Can record a bug fix."""
        mem_id = memory.episodic.bug(
            description="Race condition in auth",
            cause="Missing mutex",
            fix="Added lock",
            files=["auth/token.py"]
        )
        assert mem_id is not None


class TestSemanticMemory:
    """Tests for semantic memory layer."""

    def test_establish_knowledge(self, memory):
        """Can establish semantic knowledge."""
        mem_id = memory.learn(
            "Always use mutex locks in auth module",
            category="invariant",
            importance=0.9
        )
        assert mem_id is not None

    def test_add_warning(self, memory):
        """Can add warnings for fragile areas."""
        mem_id = memory.warn(
            area="database/migrations",
            warning="Always backup before running",
            severity=0.8
        )
        assert mem_id is not None

        warnings = memory.semantic.get_warnings()
        assert len(warnings) > 0

    def test_add_convention(self, memory):
        """Can add conventions."""
        mem_id = memory.semantic.convention(
            rule="All API endpoints return JSON",
            rationale="Consistency"
        )
        assert mem_id is not None

        conventions = memory.semantic.get_conventions()
        assert len(conventions) > 0


class TestIntentMemory:
    """Tests for intent memory layer."""

    def test_set_goal(self, memory):
        """Can set a goal."""
        intent_id = memory.goal(
            "Implement OAuth2",
            priority=2,
            constraints=["No breaking changes"]
        )
        assert intent_id is not None

        intents = memory.intent.get_active()
        assert len(intents) > 0

    def test_working_on(self, memory):
        """Can track current work."""
        intent_id = memory.working_on(
            "Token refresh endpoint",
            files=["auth/refresh.py"]
        )
        assert intent_id is not None

        current = memory.intent.get_working_on()
        assert current is not None
        assert "Token refresh" in current["description"]

    def test_done_clears_task(self, memory):
        """Done clears current task."""
        memory.working_on("Some task")
        assert memory.intent.get_working_on() is not None

        memory.done()
        assert memory.intent.get_working_on() is None


class TestSearch:
    """Tests for search functionality."""

    def test_recall_finds_memories(self, memory):
        """Recall can find memories by content."""
        memory.record("Authentication system updated")
        memory.record("Database migration complete")
        memory.learn("Auth requires special handling")

        results = memory.recall("authentication")
        assert len(results) > 0

    def test_relevant_for_task(self, memory):
        """Can get relevant memories for a task."""
        memory.learn("Auth module is fragile")
        memory.warn("auth/", "Test thoroughly")
        memory.record("Fixed auth bug")

        relevant = memory.relevant_for(
            task="fix authentication",
            files=["auth/login.py"]
        )
        assert "knowledge" in relevant
        assert "warnings" in relevant


class TestContext:
    """Tests for context generation."""

    def test_context_text_format(self, memory):
        """Can generate text context."""
        memory.goal("Test the system")
        memory.warn("tests/", "Flaky tests exist")

        context = memory.context(format="text")
        assert isinstance(context, str)
        assert "Memory Context" in context

    def test_context_json_format(self, memory):
        """Can generate JSON context."""
        memory.goal("Test the system")
        memory.learn("Tests run slowly")

        context = memory.context(format="json")
        assert isinstance(context, dict)
        assert "intent" in context
        assert "knowledge" in context


class TestStats:
    """Tests for statistics."""

    def test_stats_returns_counts(self, memory):
        """Stats returns memory counts."""
        memory.record("Event 1")
        memory.record("Event 2")
        memory.learn("Knowledge 1")

        stats = memory.stats()
        assert stats["total_memories"] >= 3
        assert "memories_by_layer" in stats


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
