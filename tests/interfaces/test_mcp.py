import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from visp_memory import Memory, MemoryConfig
from visp_memory.core.trust import Provenance, WriteChannel, provenance_tag


class TestMCPServer:
    """Tests for MCP server."""

    def test_mcp_server_creates(self):
        """MCP server can be created."""
        try:
            from visp_memory.interfaces.mcp import MCP_AVAILABLE, create_mcp_server

            if not MCP_AVAILABLE:
                pytest.skip("MCP not installed")

            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
                # An MCP server runs against an initialized project, which always has a
                # repository scope. Without one every write tool is correctly refused.
                config = MemoryConfig(repo_id="test-repo")
                config.storage.data_dir = Path(tmpdir)
                config.embedding.provider = "noop"
                memory = Memory(config=config)

                with mock.patch("visp_memory.interfaces.mcp.Memory", return_value=memory):
                    server = create_mcp_server()

            assert server is not None
            assert server.name == "visp-memory"
        except ImportError:
            pytest.skip("MCP not installed")

    @pytest.mark.asyncio
    async def test_mcp_learn_schema_is_closed_and_has_no_signing_surface(
        self, tmp_path, monkeypatch
    ):
        from mcp.types import ListToolsRequest

        from visp_memory.interfaces.mcp import MCP_AVAILABLE, create_mcp_server

        if not MCP_AVAILABLE:
            pytest.skip("MCP not installed")
        # An MCP server runs against an initialized project, which always has a
        # repository scope. Without one every write tool is correctly refused.
        config = MemoryConfig(repo_id="test-repo")
        config.storage.data_dir = tmp_path
        config.embedding.provider = "noop"
        memory = Memory(config=config)
        monkeypatch.setenv("VISP_MEMORY_MCP_PROFILE", "full")
        with mock.patch("visp_memory.interfaces.mcp.Memory", return_value=memory):
            server = create_mcp_server()
        response = await server.request_handlers[ListToolsRequest](ListToolsRequest())
        tools = {tool.name: tool for tool in response.root.tools}
        properties = tools["memory_learn"].inputSchema["properties"]

        assert properties["category"]["enum"] == [
            "fact",
            "preference",
            "procedure",
            "prohibition",
            "hypothesis",
            "negative",
        ]
        assert "epistemic_status" not in properties
        assert properties["authority_attestation"]["type"] == "string"
        assert not any("sign" in name or "attest" in name for name in tools)

    @pytest.mark.asyncio
    async def test_mcp_learn_forwards_only_opaque_attestation_and_refuses_status(self):
        from visp_memory.interfaces.mcp import handle_tool

        captured = {}

        class FakeMemory:
            # A real Memory always carries a config, and the write guard
            # resolves a scope from it. A stub that cannot fail the way the
            # real object fails is testing a different object.
            config = SimpleNamespace(repo_id="test-repo")

            def learn(self, knowledge, **kwargs):
                captured.update({"knowledge": knowledge, **kwargs})
                return "belief-1"

        result = await handle_tool(
            "memory_learn",
            {
                "knowledge": "Never bypass review",
                "category": "prohibition",
                "authority_attestation": "opaque-attestation",
            },
            FakeMemory(),
        )

        assert "belief-1" in result
        assert captured["authority_attestation"] == "opaque-attestation"
        assert "epistemic_status" not in captured
        with pytest.raises(ValueError, match="epistemic"):
            await handle_tool(
                "memory_learn",
                {"knowledge": "Fact", "epistemic_status": "observed"},
                FakeMemory(),
            )

    @pytest.mark.asyncio
    async def test_guarded_tools_advertise_string_or_array_runtime_scope(
        self, tmp_path, monkeypatch
    ):
        from mcp.types import ListToolsRequest

        from visp_memory.interfaces.mcp import (
            MCP_AVAILABLE,
            RUNTIME_SCOPE_SCHEMA,
            create_mcp_server,
        )

        if not MCP_AVAILABLE:
            pytest.skip("MCP not installed")

        config = MemoryConfig(repo_id="repo-a")
        config.storage.data_dir = tmp_path
        config.embedding.provider = "noop"
        memory = Memory(config=config)
        monkeypatch.setenv("VISP_MEMORY_MCP_PROFILE", "full")
        with mock.patch("visp_memory.interfaces.mcp.Memory", return_value=memory):
            server = create_mcp_server()
        response = await server.request_handlers[ListToolsRequest](ListToolsRequest())
        tools = {tool.name: tool for tool in response.root.tools}

        guarded_names = {
            "memory_prepare_task",
            "memory_context",
            "memory_recall",
            "memory_trace",
            "memory_neighbors",
            "memory_path",
            "memory_why_relevant",
            "memory_session_start",
            "memory_before_change",
        }
        for name in guarded_names:
            properties = tools[name].inputSchema["properties"]
            assert properties["environment"] == RUNTIME_SCOPE_SCHEMA
            assert properties["task_type"] == RUNTIME_SCOPE_SCHEMA
        for name in {
            "memory_trace",
            "memory_neighbors",
            "memory_path",
            "memory_why_relevant",
        }:
            assert tools[name].inputSchema["properties"]["as_of"] == {
                "type": "string",
                "format": "date-time",
            }

    @pytest.mark.asyncio
    async def test_session_start_filters_quarantined_context_and_relevant_memory(self):
        from visp_memory.interfaces.mcp import MCP_AVAILABLE, handle_tool

        if not MCP_AVAILABLE:
            pytest.skip("MCP not installed")

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            config = MemoryConfig(repo_id="repo-a")
            config.storage.data_dir = Path(tmpdir)
            config.embedding.provider = "noop"
            memory = Memory(config=config)
            for content, tier in (
                ("authentication trusted session rule", Provenance.DERIVED),
                ("authentication poison session rule", Provenance.EXTERNAL),
            ):
                evidence_id = memory._storage.store_evidence(content, repo_id="repo-a")
                memory._storage.store_memory(
                    content,
                    layer="semantic",
                    category="preference",
                    repo_id="repo-a",
                    tags=[provenance_tag(tier)],
                    auto_link=False,
                    evidence_ids=[evidence_id],
                )

            result = await handle_tool(
                "memory_session_start",
                {"task": "Review authentication session", "repo_id": "repo-a"},
                memory,
            )

        assert "trusted session rule" in result
        assert "poison session rule" not in result

    @pytest.mark.asyncio
    async def test_session_start_refuses_missing_scope_before_recording_task(self):
        from visp_memory.interfaces.mcp import MCP_AVAILABLE, handle_tool

        if not MCP_AVAILABLE:
            pytest.skip("MCP not installed")

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            config = MemoryConfig(repo_id=None)
            config.storage.data_dir = Path(tmpdir)
            config.embedding.provider = "noop"
            memory = Memory(config=config)

            with pytest.raises(ValueError, match="repo_id.*required"):
                await handle_tool(
                    "memory_session_start",
                    {"task": "Must not create an unscoped task"},
                    memory,
                )

            assert memory._storage.get_active_intents() == []

    @pytest.mark.asyncio
    async def test_mcp_handle_tool(self):
        """MCP tool handler works."""
        try:
            from visp_memory.interfaces.mcp import MCP_AVAILABLE, handle_tool

            if not MCP_AVAILABLE:
                pytest.skip("MCP not installed")

            # Create a temp memory for testing
            import tempfile

            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
                # An MCP server runs against an initialized project, which always has a
                # repository scope. Without one every write tool is correctly refused.
                config = MemoryConfig(repo_id="test-repo")
                config.storage.data_dir = Path(tmpdir)
                config.embedding.provider = "noop"
                memory = Memory(config=config)

                # Test memory_stats tool
                result = await handle_tool("memory_stats", {}, memory)
                assert "total_memories" in result
        except ImportError:
            pytest.skip("MCP not installed")

    @pytest.mark.asyncio
    async def test_mcp_prepare_task_returns_cited_brief_and_delta(self):
        try:
            from visp_memory.interfaces.mcp import MCP_AVAILABLE, handle_tool

            if not MCP_AVAILABLE:
                pytest.skip("MCP not installed")

            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
                # An MCP server runs against an initialized project, which always has a
                # repository scope. Without one every write tool is correctly refused.
                config = MemoryConfig(repo_id="test-repo")
                config.storage.data_dir = Path(tmpdir)
                config.repo_id = "brief-repo"
                config.embedding.provider = "noop"
                memory = Memory(config=config)
                memory.learn(
                    "Use repository-scoped HttpOnly sessions",
                    repo_id="brief-repo",
                    _write_channel=WriteChannel.MCP,
                )

                raw = await handle_tool(
                    "memory_prepare_task",
                    {
                        "task": "Review repository authentication",
                        "repo_id": "brief-repo",
                        "token_budget": 300,
                        "format": "json",
                    },
                    memory,
                )
                brief = json.loads(raw)
                unchanged = await handle_tool(
                    "memory_prepare_task",
                    {
                        "task": "Review repository authentication",
                        "repo_id": "brief-repo",
                        "token_budget": 300,
                        "previous_fingerprint": brief["fingerprint"],
                    },
                    memory,
                )

            assert brief["schema_version"] == "1.0"
            assert brief["citations"]
            assert "Task Memory Brief" in brief["context"]
            assert "Task brief unchanged" in unchanged
        except ImportError:
            pytest.skip("MCP not installed")

    @pytest.mark.asyncio
    async def test_mcp_handle_record(self):
        """MCP record tool works."""
        try:
            from visp_memory.interfaces.mcp import MCP_AVAILABLE, handle_tool

            if not MCP_AVAILABLE:
                pytest.skip("MCP not installed")

            import tempfile

            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
                # An MCP server runs against an initialized project, which always has a
                # repository scope. Without one every write tool is correctly refused.
                config = MemoryConfig(repo_id="test-repo")
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
    async def test_mcp_durable_writes_assign_assisted_provenance(self):
        try:
            from visp_memory.core.trust import Provenance, provenance_of
            from visp_memory.interfaces.mcp import MCP_AVAILABLE, handle_tool

            if not MCP_AVAILABLE:
                pytest.skip("MCP not installed")

            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
                # An MCP server runs against an initialized project, which always has a
                # repository scope. Without one every write tool is correctly refused.
                config = MemoryConfig(repo_id="test-repo")
                config.storage.data_dir = Path(tmpdir)
                config.embedding.provider = "noop"
                memory = Memory(config=config)

                calls = [
                    ("memory_record", {"event": "MCP event", "tags": ["provenance:authored"]}),
                    ("memory_decision", {"what": "MCP decision", "why": "MCP reason"}),
                    ("memory_learn", {"knowledge": "MCP knowledge"}),
                    ("memory_warn", {"area": "mcp.py", "warning": "MCP warning"}),
                    ("memory_issue", {"issue": "MCP issue"}),
                    (
                        "memory_after_work",
                        {
                            "summary": "MCP work summary",
                            "decisions": ["MCP after-work decision"],
                            "bugs_fixed": ["MCP after-work bug"],
                            "warnings": [{"area": "after.py", "warning": "After warning"}],
                        },
                    ),
                ]
                for name, args in calls:
                    result = await handle_tool(name, args, memory)
                    assert "Unknown" not in result

                stored = memory._storage.list_memories(limit=100)

            assert len(stored) == 9
            provenances = {provenance_of(item) for item in stored}

            # This assertion used to read `== {Provenance.UNKNOWN}`, and it could
            # never have failed for the reason the test is named for. The fixture
            # built an UNSCOPED store, where every row is quarantined as UNKNOWN
            # whatever channel wrote it — so a bug that assigned MCP writes the
            # wrong provenance would have sailed straight through. Once the
            # fixture carries a scope (as any real MCP server does), the channel
            # policy actually takes effect and the durable writes come back
            # ASSISTED, which is what the test's name claims all along.
            #
            # The security property, stated directly: the first call claims
            # `provenance:authored` in its tags, and that claim must not survive.
            # A caller does not get to promote its own writes to human-authored.
            assert Provenance.AUTHORED not in provenances, (
                "A caller-supplied provenance:authored tag survived an MCP write. "
                "MCP content would then be indistinguishable from what a human wrote."
            )
            assert Provenance.ASSISTED in provenances, (
                "No MCP write was labelled assisted, so the channel policy is not being applied."
            )
            # The earlier note here recorded that `memory_issue` still landed as
            # UNKNOWN, pending investigation. It was not a provenance-labelling
            # quirk: `memory_issue` calls the LAYER (semantic.known_issue) while
            # its siblings call the FACADE (memory.learn/memory.warn), and only
            # the facade consults config.repo_id. So it wrote repo_id=None,
            # storage substituted the quarantine sentinel, and the row was
            # stamped UNKNOWN as a consequence of being quarantined. Every known
            # issue recorded through MCP was lost that way. The dispatch guard
            # now pins the resolved scope into args, so no row here is UNKNOWN.
            assert Provenance.UNKNOWN not in provenances, (
                "An MCP write was quarantined in a scoped project, so its content is "
                "unrecallable while the tool reported success."
            )
        except ImportError:
            pytest.skip("MCP not installed")

    @pytest.mark.asyncio
    async def test_mcp_feedback_tools_log_inspect_and_reset(self):
        """MCP feedback tools expose recall utility controls."""
        try:
            from visp_memory.interfaces.mcp import MCP_AVAILABLE, handle_tool

            if not MCP_AVAILABLE:
                pytest.skip("MCP not installed")

            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
                config = MemoryConfig(repo_id="repo-a")
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

    def test_http_feedback_tools_preflight_scope_and_preserve_repository_boundaries(
        self, tmp_path
    ):
        """Direct dispatch has the same HTTP scope contract as the transport."""
        from visp_memory.interfaces.mcp import (
            MCPAuthorizationError,
            MCPRequestContext,
            _dispatch_tool,
            bind_mcp_request_context,
        )
        from visp_memory.server.auth import UserContext

        config = MemoryConfig(repo_id="repo-a")
        config.storage.data_dir = Path(tmp_path)
        config.embedding.provider = "noop"
        memory = Memory(config=config)
        repo_a_memory = memory.record("repo-a tool feedback", repo_id="repo-a")
        repo_b_memory = memory.record("repo-b tool feedback", repo_id="repo-b")
        memory.record_utility_feedback(repo_a_memory, "used", repo_id="repo-a")
        memory.record_utility_feedback(repo_b_memory, "used", repo_id="repo-b")
        context = MCPRequestContext(
            transport="http",
            principal=UserContext(
                user_id="local",
                username="local",
                is_admin=True,
                auth_type="local",
            ),
        )

        with bind_mcp_request_context(context):
            with pytest.raises(MCPAuthorizationError):
                _dispatch_tool("memory_feedback_inspect", {}, memory)
            report = json.loads(
                _dispatch_tool(
                    "memory_feedback_inspect", {"repo_id": "repo-a"}, memory
                )
            )
            with pytest.raises(MCPAuthorizationError):
                _dispatch_tool("memory_feedback_reset", {"confirm": True}, memory)
            deleted = _dispatch_tool(
                "memory_feedback_reset",
                {"repo_id": "repo-a", "confirm": True},
                memory,
            )

        assert report["summary"]["total_events"] == 1
        assert [event["memory_id"] for event in report["events"]] == [repo_a_memory]
        assert "Deleted 1 feedback events." == deleted
        assert memory._storage.inspect_recall_utility(repo_id="repo-b")["summary"][
            "total_events"
        ] == 1

    @pytest.mark.asyncio
    async def test_mcp_clear_goals_requires_confirmation_and_preserves_status(self):
        """memory_clear_goals records assisted history and never clears status."""
        try:
            from visp_memory.interfaces.mcp import MCP_AVAILABLE, handle_tool

            if not MCP_AVAILABLE:
                pytest.skip("MCP not installed")

            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
                # An MCP server runs against an initialized project, which always has a
                # repository scope. Without one every write tool is correctly refused.
                config = MemoryConfig(repo_id="test-repo")
                config.storage.data_dir = Path(tmpdir)
                config.embedding.provider = "noop"
                memory = Memory(config=config)
                memory.goal("Ship the release")

                guarded = await handle_tool("memory_clear_goals", {}, memory)
                assert "confirm=true" in guarded

                # The goal must still exist, so confirming records exactly one outcome.
                recorded = await handle_tool(
                    "memory_clear_goals", {"confirm": True}, memory
                )
                assert "Recorded 1 goal outcomes" in recorded
                goal = memory.intent.get_active()[0]
                assert goal["status"] == "active"
                outcome = goal["context"]["outcome_history"][-1]
                assert outcome["provenance"]["channel"] == "mcp"
                assert outcome["status_changed"] is False
        except ImportError:
            pytest.skip("MCP not installed")

    @pytest.mark.asyncio
    async def test_mcp_recall_outputs_ranking_factors(self):
        """MCP recall exposes intent-aware ranking explanations."""
        try:
            from visp_memory.interfaces.mcp import MCP_AVAILABLE, handle_tool

            if not MCP_AVAILABLE:
                pytest.skip("MCP not installed")

            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
                config = MemoryConfig(repo_id="repo-a")
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
            from visp_memory.interfaces.mcp import MCP_AVAILABLE, handle_tool

            if not MCP_AVAILABLE:
                pytest.skip("MCP not installed")

            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
                # An MCP server runs against an initialized project, which always has a
                # repository scope. Without one every write tool is correctly refused.
                config = MemoryConfig(repo_id="test-repo")
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
            from visp_memory.interfaces.mcp import MCP_AVAILABLE, handle_tool

            if not MCP_AVAILABLE:
                pytest.skip("MCP not installed")

            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
                config = MemoryConfig(repo_id="repo-a")
                config.storage.data_dir = Path(tmpdir)
                config.embedding.provider = "noop"
                memory = Memory(config=config)
                memory.warn(
                    "src/server.py",
                    "Check auth before changing routes",
                    _write_channel=WriteChannel.MCP,
                )

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
    async def test_mcp_update_records_close_request_and_decay_preview(self):
        """MCP can update content but only records requested workflow outcomes."""
        try:
            from visp_memory.interfaces.mcp import MCP_AVAILABLE, handle_tool

            if not MCP_AVAILABLE:
                pytest.skip("MCP not installed")

            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
                # An MCP server runs against an initialized project, which always has a
                # repository scope. Without one every write tool is correctly refused.
                config = MemoryConfig(repo_id="test-repo")
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
                assert "status unchanged" in close.lower()
                assert "likely_to_decay" in preview
                assert memory_id in preview
                active = next(
                    item for item in memory.intent.get_active() if item["id"] == intent_id
                )
                outcome = active["context"]["outcome_history"][-1]
                assert outcome["outcome"] == "closed"
                assert outcome["provenance"]["channel"] == "mcp"
                assert outcome["status_changed"] is False
        except ImportError:
            pytest.skip("MCP not installed")

    def test_mcp_relevant_memory_formats_relationship_evidence(self):
        """MCP recall-oriented output includes relationship evidence when available."""
        from visp_memory.interfaces.mcp import _format_relevant_memory

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            config = MemoryConfig(repo_id="repo-a")
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
        from visp_memory.interfaces.mcp import handle_tool

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            config = MemoryConfig(repo_id="repo-a")
            config.storage.data_dir = Path(tmpdir)
            config.embedding.provider = "noop"
            memory = Memory(config=config)

            source_id = memory.record(
                "Trace recall should explain auth routes",
                _write_channel=WriteChannel.MCP,
            )
            target_id = memory.record(
                "Repository scope evidence matters for auth routes",
                _write_channel=WriteChannel.MCP,
            )
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
                {
                    "query": "trace auth routes",
                    "repo_id": "repo-a",
                    "depth": 1,
                    "token_budget": 1000,
                },
                memory,
            )

        assert "# Graph Recall: trace" in result
        assert source_id in result
        assert target_id in result
        assert "Relationship reason appears in MCP trace output." in result


class TestMCPToolProfile:
    """Tests for the configurable MCP tool surface (token-efficiency profiles)."""

    def test_resolve_profile_defaults_to_core(self, monkeypatch):
        """Default is the lean surface: tool definitions cost context on every session."""
        from visp_memory.interfaces.mcp import _resolve_tool_profile

        monkeypatch.delenv("VISP_MEMORY_MCP_PROFILE", raising=False)
        assert _resolve_tool_profile() == "core"

    def test_resolve_profile_honors_core(self, monkeypatch):
        from visp_memory.interfaces.mcp import _resolve_tool_profile

        monkeypatch.setenv("VISP_MEMORY_MCP_PROFILE", "Core")
        assert _resolve_tool_profile() == "core"

    def test_resolve_profile_falls_back_on_unknown(self, monkeypatch):
        from visp_memory.interfaces.mcp import _resolve_tool_profile

        monkeypatch.setenv("VISP_MEMORY_MCP_PROFILE", "tiny")
        assert _resolve_tool_profile() == "core"

    def test_resolve_profile_trims_whitespace_and_handles_empty(self, monkeypatch):
        from visp_memory.interfaces.mcp import _resolve_tool_profile

        monkeypatch.setenv("VISP_MEMORY_MCP_PROFILE", "  core  ")
        assert _resolve_tool_profile() == "core"

        monkeypatch.setenv("VISP_MEMORY_MCP_PROFILE", "")
        assert _resolve_tool_profile() == "core"

    def test_filter_core_is_strict_subset_of_full(self):
        from types import SimpleNamespace

        from visp_memory.interfaces.mcp import (
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
        from visp_memory.interfaces.mcp import (
            CORE_TOOL_NAMES,
            MCP_AVAILABLE,
            handle_tool,
        )

        if not MCP_AVAILABLE:
            pytest.skip("MCP not installed")

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            # An MCP server runs against an initialized project, which always has a
            # repository scope. Without one every write tool is correctly refused.
            config = MemoryConfig(repo_id="test-repo")
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
            from visp_memory.interfaces.mcp import MCP_AVAILABLE, handle_tool

            if not MCP_AVAILABLE:
                pytest.skip("MCP not installed")

            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
                # An MCP server runs against an initialized project, which always has a
                # repository scope. Without one every write tool is correctly refused.
                config = MemoryConfig(repo_id="test-repo")
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
            from visp_memory.interfaces.mcp import MCP_AVAILABLE, handle_tool

            if not MCP_AVAILABLE:
                pytest.skip("MCP not installed")

            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
                # An MCP server runs against an initialized project, which always has a
                # repository scope. Without one every write tool is correctly refused.
                config = MemoryConfig(repo_id="test-repo")
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
            from visp_memory.interfaces.mcp import MCP_AVAILABLE, handle_tool

            if not MCP_AVAILABLE:
                pytest.skip("MCP not installed")

            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
                # An MCP server runs against an initialized project, which always has a
                # repository scope. Without one every write tool is correctly refused.
                config = MemoryConfig(repo_id="test-repo")
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
    async def test_mcp_update_intent_records_valid_status_without_transition(self):
        """A valid status becomes assisted history, not workflow state."""
        try:
            from visp_memory.interfaces.mcp import MCP_AVAILABLE, handle_tool

            if not MCP_AVAILABLE:
                pytest.skip("MCP not installed")

            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
                # An MCP server runs against an initialized project, which always has a
                # repository scope. Without one every write tool is correctly refused.
                config = MemoryConfig(repo_id="test-repo")
                config.storage.data_dir = Path(tmpdir)
                config.embedding.provider = "noop"
                memory = Memory(config=config)
                intent_id = memory.goal("Ship the release")

                result = await handle_tool(
                    "memory_update_intent",
                    {"intent_id": intent_id, "status": "completed"},
                    memory,
                )
                active = next(i for i in memory.intent.get_active() if i["id"] == intent_id)

            assert "status unchanged" in result.lower()
            assert active["status"] == "active"
            outcome = active["context"]["outcome_history"][-1]
            assert outcome["outcome"] == "completed"
            assert outcome["provenance"]["source"] == "assisted"
            assert outcome["status_changed"] is False
        except ImportError:
            pytest.skip("MCP not installed")

    @pytest.mark.asyncio
    async def test_mcp_done_records_outcome_without_clearing_task(self):
        try:
            from visp_memory.interfaces.mcp import MCP_AVAILABLE, handle_tool

            if not MCP_AVAILABLE:
                pytest.skip("MCP not installed")

            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
                # An MCP server runs against an initialized project, which always has a
                # repository scope. Without one every write tool is correctly refused.
                config = MemoryConfig(repo_id="test-repo")
                config.storage.data_dir = Path(tmpdir)
                config.embedding.provider = "noop"
                memory = Memory(config=config)
                intent_id = memory.working_on("Keep workflow authority external")

                result = await handle_tool("memory_done", {}, memory)
                active = next(i for i in memory.intent.get_active() if i["id"] == intent_id)

            assert "status unchanged" in result.lower()
            assert active["status"] == "active"
            assert active["context"]["outcome_history"][-1]["provenance"]["channel"] == "mcp"
        except ImportError:
            pytest.skip("MCP not installed")

    @pytest.mark.asyncio
    async def test_mcp_recall_rejects_invalid_layers(self):
        """memory_recall must reject an unknown layer in the layers filter."""
        try:
            from visp_memory.interfaces.mcp import MCP_AVAILABLE, handle_tool

            if not MCP_AVAILABLE:
                pytest.skip("MCP not installed")

            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
                # An MCP server runs against an initialized project, which always has a
                # repository scope. Without one every write tool is correctly refused.
                config = MemoryConfig(repo_id="test-repo")
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
            from visp_memory.interfaces.mcp import MCP_AVAILABLE, handle_tool

            if not MCP_AVAILABLE:
                pytest.skip("MCP not installed")

            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
                config = MemoryConfig(repo_id="repo-a")
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
