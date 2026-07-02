import json
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

            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
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

            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
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

            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
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
    async def test_mcp_feedback_tools_log_inspect_and_reset(self):
        """MCP feedback tools expose recall utility controls."""
        try:
            from llm_memory.interfaces.mcp import MCP_AVAILABLE, handle_tool

            if not MCP_AVAILABLE:
                pytest.skip("MCP not installed")

            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
                config = MemoryConfig()
                config.storage.data_dir = Path(tmpdir)
                config.embedding.provider = "noop"
                memory = Memory(config=config)
                memory_id = memory.record("Fixed authentication bug")

                recall = await handle_tool(
                    "memory_recall",
                    {"query": "authentication", "log_utility": True},
                    memory,
                )
                used = await handle_tool(
                    "memory_feedback_log",
                    {"memory_id": memory_id, "event_type": "used"},
                    memory,
                )
                inspect = await handle_tool("memory_feedback_inspect", {}, memory)
                reset = await handle_tool(
                    "memory_feedback_reset",
                    {"memory_id": memory_id, "confirm": True},
                    memory,
                )

                report = json.loads(inspect)

            assert memory_id in recall
            assert "Recorded 1 feedback events" in used
            assert report["summary"]["by_event_type"] == {"surfaced": 1, "used": 1}
            assert "Deleted 2 feedback events" in reset
        except ImportError:
            pytest.skip("MCP not installed")

    @pytest.mark.asyncio
    async def test_mcp_clear_goals_requires_confirmation(self):
        """memory_clear_goals must not wipe goals without confirm=true."""
        try:
            from llm_memory.interfaces.mcp import MCP_AVAILABLE, handle_tool

            if not MCP_AVAILABLE:
                pytest.skip("MCP not installed")

            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
                config = MemoryConfig()
                config.storage.data_dir = Path(tmpdir)
                config.embedding.provider = "noop"
                memory = Memory(config=config)
                memory.goal("Ship the release")

                guarded = await handle_tool("memory_clear_goals", {}, memory)
                assert "confirm=true" in guarded

                # The goal must still exist, so confirming now clears exactly one.
                cleared = await handle_tool(
                    "memory_clear_goals", {"confirm": True}, memory
                )
                assert "Cleared 1 goals" in cleared
        except ImportError:
            pytest.skip("MCP not installed")

    @pytest.mark.asyncio
    async def test_mcp_recall_outputs_ranking_factors(self):
        """MCP recall exposes intent-aware ranking explanations."""
        try:
            from llm_memory.interfaces.mcp import MCP_AVAILABLE, handle_tool

            if not MCP_AVAILABLE:
                pytest.skip("MCP not installed")

            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
                config = MemoryConfig()
                config.storage.data_dir = Path(tmpdir)
                config.embedding.provider = "noop"
                memory = Memory(config=config)
                memory.goal("Stabilize auth refresh", constraints=["No breaking API"])
                memory_id = memory.record(
                    "Auth refresh fix in auth/refresh.py respects No breaking API",
                    context={"files": ["auth/refresh.py"], "session_id": "session-1"},
                )

                result = await handle_tool(
                    "memory_recall",
                    {
                        "query": "auth refresh",
                        "task": "Fix auth refresh",
                        "files": ["auth/refresh.py"],
                        "session_id": "session-1",
                        "constraints": ["No breaking API"],
                    },
                    memory,
                )

            assert memory_id in result
            assert "Ranking factors:" in result
            assert "file:" in result
            assert "active_intent:" in result
        except ImportError:
            pytest.skip("MCP not installed")

    @pytest.mark.asyncio
    async def test_mcp_remember_returns_latest_memory(self):
        """MCP remember tool returns the newest active memory."""
        try:
            from llm_memory.interfaces.mcp import MCP_AVAILABLE, handle_tool

            if not MCP_AVAILABLE:
                pytest.skip("MCP not installed")

            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
                config = MemoryConfig()
                config.storage.data_dir = Path(tmpdir)
                config.embedding.provider = "noop"
                memory = Memory(config=config)
                memory.record("Older MCP memory")
                latest_id = memory.record("Latest MCP memory")

                result = await handle_tool("memory_remember", {}, memory)

                assert "Latest memory" in result
                assert latest_id in result
                assert "Latest MCP memory" in result
        except ImportError:
            pytest.skip("MCP not installed")

    @pytest.mark.asyncio
    async def test_mcp_workflow_before_and_after_work(self):
        """Codex workflow tools recall before edits and record after work."""
        try:
            from llm_memory.interfaces.mcp import MCP_AVAILABLE, handle_tool

            if not MCP_AVAILABLE:
                pytest.skip("MCP not installed")

            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
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

    @pytest.mark.asyncio
    async def test_mcp_update_close_intent_and_decay_preview(self):
        """MCP exposes intent lifecycle controls and decay preview."""
        try:
            from llm_memory.interfaces.mcp import MCP_AVAILABLE, handle_tool

            if not MCP_AVAILABLE:
                pytest.skip("MCP not installed")

            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
                config = MemoryConfig()
                config.storage.data_dir = Path(tmpdir)
                config.embedding.provider = "noop"
                memory = Memory(config=config)
                memory_id = memory.record("Old unused MCP memory", importance=0.8)
                intent_id = memory.goal("Original MCP goal")

                with memory._storage._get_db() as conn:
                    conn.execute(
                        "UPDATE memories SET accessed_at = '2020-01-01 00:00:00' WHERE id = ?",
                        (memory_id,),
                    )
                    conn.commit()

                update = await handle_tool(
                    "memory_update_intent",
                    {"intent_id": intent_id, "description": "Updated MCP goal"},
                    memory,
                )
                close = await handle_tool("memory_close_intent", {"intent_id": intent_id}, memory)
                preview = await handle_tool(
                    "memory_decay_preview",
                    {"limit": 5, "halflife_days": 30},
                    memory,
                )

                assert "Intent updated" in update
                assert "Intent closed" in close
                assert "likely_to_decay" in preview
                assert memory_id in preview
        except ImportError:
            pytest.skip("MCP not installed")

    def test_mcp_relevant_memory_formats_relationship_evidence(self):
        """MCP recall-oriented output includes relationship evidence when available."""
        from llm_memory.interfaces.mcp import _format_relevant_memory

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            config = MemoryConfig()
            config.storage.data_dir = Path(tmpdir)
            config.embedding.provider = "noop"
            memory = Memory(config=config)

            source_id = memory.record("Route handlers must validate repository scope")
            target_id = memory.record("Relationship evidence should be visible to MCP clients")
            memory._storage.add_relationship(
                source_id,
                target_id,
                "supports",
                strength=0.9,
                evidence={
                    "confidence": "observed",
                    "confidence_score": 0.95,
                    "source": "test",
                    "reason": "The MCP output should explain why this edge is relevant.",
                },
            )

            source = memory._storage.get_memory(source_id)
            result = _format_relevant_memory(
                {"knowledge": [source], "warnings": [], "history": []}, memory
            )

        assert "Route handlers must validate repository scope" in result
        assert (
            "Relationship evidence (supports -> Relationship evidence should be visible" in result
        )
        assert "confidence=observed" in result
        assert "score=0.95" in result
        assert "source=test" in result
        assert "why this edge is relevant" in result

    @pytest.mark.asyncio
    async def test_mcp_memory_trace_outputs_relationship_reasons(self):
        """MCP trace output includes graph relationship reasons."""
        from llm_memory.interfaces.mcp import handle_tool

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            config = MemoryConfig()
            config.storage.data_dir = Path(tmpdir)
            config.embedding.provider = "noop"
            memory = Memory(config=config)

            source_id = memory.record("Trace recall should explain auth routes")
            target_id = memory.record("Repository scope evidence matters for auth routes")
            memory._storage.add_relationship(
                source_id,
                target_id,
                "supports",
                evidence={
                    "confidence": "observed",
                    "confidence_score": 0.9,
                    "reason": "Relationship reason appears in MCP trace output.",
                },
            )

            result = await handle_tool(
                "memory_trace",
                {"query": "trace auth routes", "depth": 1, "token_budget": 1000},
                memory,
            )

        assert "# Graph Recall: trace" in result
        assert source_id in result
        assert target_id in result
        assert "Relationship reason appears in MCP trace output." in result


class TestMCPToolProfile:
    """Tests for the configurable MCP tool surface (token-efficiency profiles)."""

    def test_resolve_profile_defaults_to_full(self, monkeypatch):
        from llm_memory.interfaces.mcp import _resolve_tool_profile

        monkeypatch.delenv("LLM_MEMORY_MCP_PROFILE", raising=False)
        assert _resolve_tool_profile() == "full"

    def test_resolve_profile_honors_core(self, monkeypatch):
        from llm_memory.interfaces.mcp import _resolve_tool_profile

        monkeypatch.setenv("LLM_MEMORY_MCP_PROFILE", "Core")
        assert _resolve_tool_profile() == "core"

    def test_resolve_profile_falls_back_on_unknown(self, monkeypatch):
        from llm_memory.interfaces.mcp import _resolve_tool_profile

        monkeypatch.setenv("LLM_MEMORY_MCP_PROFILE", "tiny")
        assert _resolve_tool_profile() == "full"

    def test_resolve_profile_trims_whitespace_and_handles_empty(self, monkeypatch):
        from llm_memory.interfaces.mcp import _resolve_tool_profile

        monkeypatch.setenv("LLM_MEMORY_MCP_PROFILE", "  core  ")
        assert _resolve_tool_profile() == "core"

        monkeypatch.setenv("LLM_MEMORY_MCP_PROFILE", "")
        assert _resolve_tool_profile() == "full"

    def test_filter_core_is_strict_subset_of_full(self):
        from types import SimpleNamespace

        from llm_memory.interfaces.mcp import (
            CORE_TOOL_NAMES,
            _filter_tools_by_profile,
        )

        catalog = [
            SimpleNamespace(name=name)
            for name in [*CORE_TOOL_NAMES, "memory_stats", "memory_decay", "memory_path"]
        ]

        full = _filter_tools_by_profile(catalog, "full")
        core = _filter_tools_by_profile(catalog, "core")

        assert len(full) == len(catalog)
        assert {tool.name for tool in core} == set(CORE_TOOL_NAMES)
        assert len(core) < len(full)

    @pytest.mark.asyncio
    async def test_every_core_tool_name_is_routable(self):
        """Guard against typos: each advertised core tool must reach a handler."""
        from llm_memory.interfaces.mcp import (
            CORE_TOOL_NAMES,
            MCP_AVAILABLE,
            handle_tool,
        )

        if not MCP_AVAILABLE:
            pytest.skip("MCP not installed")

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            config = MemoryConfig()
            config.storage.data_dir = Path(tmpdir)
            config.embedding.provider = "noop"
            memory = Memory(config=config)

            for name in CORE_TOOL_NAMES:
                try:
                    result = await handle_tool(name, {}, memory)
                except Exception:
                    # Reached a handler that requires arguments; the name is valid.
                    continue
                assert not result.startswith("Unknown tool"), name

    @pytest.mark.asyncio
    async def test_mcp_remember_rejects_invalid_layer(self):
        """memory_remember must reject an out-of-vocabulary layer, not silently no-op."""
        try:
            from llm_memory.interfaces.mcp import MCP_AVAILABLE, handle_tool

            if not MCP_AVAILABLE:
                pytest.skip("MCP not installed")

            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
                config = MemoryConfig()
                config.storage.data_dir = Path(tmpdir)
                config.embedding.provider = "noop"
                memory = Memory(config=config)
                memory.record("Some memory")

                result = await handle_tool(
                    "memory_remember", {"layer": "bogus"}, memory
                )

            assert result.startswith("Error:")
            assert "bogus" in result
        except ImportError:
            pytest.skip("MCP not installed")

    @pytest.mark.asyncio
    async def test_mcp_remember_accepts_valid_layer(self):
        """A valid layer filter still works on memory_remember."""
        try:
            from llm_memory.interfaces.mcp import MCP_AVAILABLE, handle_tool

            if not MCP_AVAILABLE:
                pytest.skip("MCP not installed")

            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
                config = MemoryConfig()
                config.storage.data_dir = Path(tmpdir)
                config.embedding.provider = "noop"
                memory = Memory(config=config)
                memory.record("Some memory")

                result = await handle_tool(
                    "memory_remember", {"layer": "episodic"}, memory
                )

            assert not result.startswith("Error:")
        except ImportError:
            pytest.skip("MCP not installed")

    @pytest.mark.asyncio
    async def test_mcp_update_intent_rejects_invalid_status(self):
        """memory_update_intent must reject an invalid status like the CLI does."""
        try:
            from llm_memory.interfaces.mcp import MCP_AVAILABLE, handle_tool

            if not MCP_AVAILABLE:
                pytest.skip("MCP not installed")

            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
                config = MemoryConfig()
                config.storage.data_dir = Path(tmpdir)
                config.embedding.provider = "noop"
                memory = Memory(config=config)
                intent_id = memory.goal("Ship the release")

                result = await handle_tool(
                    "memory_update_intent",
                    {"intent_id": intent_id, "status": "done"},
                    memory,
                )

                # The intent must be untouched by the rejected update: it is
                # still active.
                active_ids = {i["id"] for i in memory.intent.get_active()}

            assert result.startswith("Error:")
            assert "done" in result
            assert intent_id in active_ids
        except ImportError:
            pytest.skip("MCP not installed")

    @pytest.mark.asyncio
    async def test_mcp_update_intent_accepts_valid_status(self):
        """A valid status update goes through on memory_update_intent."""
        try:
            from llm_memory.interfaces.mcp import MCP_AVAILABLE, handle_tool

            if not MCP_AVAILABLE:
                pytest.skip("MCP not installed")

            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
                config = MemoryConfig()
                config.storage.data_dir = Path(tmpdir)
                config.embedding.provider = "noop"
                memory = Memory(config=config)
                intent_id = memory.goal("Ship the release")

                result = await handle_tool(
                    "memory_update_intent",
                    {"intent_id": intent_id, "status": "completed"},
                    memory,
                )
                # A completed intent is no longer active.
                active_ids = {i["id"] for i in memory.intent.get_active()}

            assert "Intent updated" in result
            assert intent_id not in active_ids
        except ImportError:
            pytest.skip("MCP not installed")

    @pytest.mark.asyncio
    async def test_mcp_recall_rejects_invalid_layers(self):
        """memory_recall must reject an unknown layer in the layers filter."""
        try:
            from llm_memory.interfaces.mcp import MCP_AVAILABLE, handle_tool

            if not MCP_AVAILABLE:
                pytest.skip("MCP not installed")

            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
                config = MemoryConfig()
                config.storage.data_dir = Path(tmpdir)
                config.embedding.provider = "noop"
                memory = Memory(config=config)
                memory.record("Auth refresh fix")

                result = await handle_tool(
                    "memory_recall",
                    {"query": "auth", "layers": ["semantic", "bogus"]},
                    memory,
                )

            assert result.startswith("Error:")
            assert "bogus" in result
        except ImportError:
            pytest.skip("MCP not installed")

    @pytest.mark.asyncio
    async def test_mcp_recall_applies_layers_filter(self):
        """memory_recall passes a valid layers filter through to Memory.recall."""
        try:
            from llm_memory.interfaces.mcp import MCP_AVAILABLE, handle_tool

            if not MCP_AVAILABLE:
                pytest.skip("MCP not installed")

            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
                config = MemoryConfig()
                config.storage.data_dir = Path(tmpdir)
                config.embedding.provider = "noop"
                memory = Memory(config=config)

                # A semantic (knowledge) memory and an episodic (event) memory.
                memory.learn("Auth tokens rotate every 15 minutes")
                memory.record("Fixed auth refresh bug")

                with mock.patch.object(
                    memory, "recall", wraps=memory.recall
                ) as recall_spy:
                    result = await handle_tool(
                        "memory_recall",
                        {"query": "auth", "layers": ["semantic"]},
                        memory,
                    )

            # The filter must reach Memory.recall verbatim.
            assert recall_spy.call_args.kwargs["layers"] == ["semantic"]
            assert not result.startswith("Error:")
        except ImportError:
            pytest.skip("MCP not installed")
