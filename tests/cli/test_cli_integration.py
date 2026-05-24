"""
Integration tests for CLI commands.

These tests verify that CLI commands work end-to-end and catch regressions
that unit tests might miss (e.g., parameter mismatches between layers).
"""

import json
import tempfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from llm_memory.interfaces.cli import app

runner = CliRunner()


@pytest.fixture
def temp_dir():
    """Create a temporary directory for each test."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def cli_env(temp_dir):
    """Set up environment for CLI tests."""
    # Change to temp directory
    import os

    old_cwd = os.getcwd()
    os.chdir(temp_dir)

    # Set backend to sqlite to avoid Neo4j dependency
    os.environ["LLM_MEMORY_STORAGE_BACKEND"] = "sqlite"
    os.environ["LLM_MEMORY_EMBEDDING_PROVIDER"] = "noop"

    # Reset the global _memory instance in CLI module to avoid caching issues between tests
    import llm_memory.interfaces.cli as cli_module

    cli_module._memory = None

    yield temp_dir

    # Restore
    cli_module._memory = None
    os.chdir(old_cwd)
    if "LLM_MEMORY_STORAGE_BACKEND" in os.environ:
        del os.environ["LLM_MEMORY_STORAGE_BACKEND"]
    if "LLM_MEMORY_EMBEDDING_PROVIDER" in os.environ:
        del os.environ["LLM_MEMORY_EMBEDDING_PROVIDER"]


class TestCLIBasicCommands:
    """Test basic CLI commands that were previously broken."""

    def test_version_command(self, cli_env):
        """CLI exposes the package version for release verification."""
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert "llm-memory" in result.output

    def test_init_command(self, cli_env):
        """Test that init command works without Neo4j."""
        result = runner.invoke(app, ["init", "--type", "code"])
        assert result.exit_code == 0
        assert "Initialized LLM Memory" in result.output
        assert Path("llm-memory.yaml").exists()

    def test_record_command(self, cli_env):
        """Test recording a memory."""
        # Init first
        runner.invoke(app, ["init", "--type", "code"])

        # Record
        result = runner.invoke(
            app,
            [
                "record",
                "Fixed authentication bug",
                "--category",
                "bug_fixed",
                "--importance",
                "0.8",
            ],
        )
        assert result.exit_code == 0
        assert "Recorded" in result.output

    def test_decision_command(self, cli_env):
        """Test recording a decision."""
        runner.invoke(app, ["init", "--type", "code"])

        result = runner.invoke(app, ["decision", "Use PostgreSQL", "Need ACID compliance"])
        assert result.exit_code == 0
        assert "Decision recorded" in result.output

    def test_warn_command(self, cli_env):
        """Test adding a warning."""
        runner.invoke(app, ["init", "--type", "code"])

        result = runner.invoke(app, ["warn", "auth/token.py", "Race condition possible"])
        assert result.exit_code == 0
        assert "Warning added" in result.output

    def test_learn_command(self, cli_env):
        """Test establishing semantic knowledge."""
        runner.invoke(app, ["init", "--type", "code"])

        result = runner.invoke(
            app, ["learn", "Always use prepared statements", "--category", "invariant"]
        )
        assert result.exit_code == 0
        assert "Established" in result.output

    def test_repo_option_does_not_leak_between_commands(self, cli_env):
        """A --repo override should apply only to the command that supplied it."""
        runner.invoke(app, ["init", "--type", "code", "--repo", "default-repo"])

        result_a = runner.invoke(app, ["learn", "Repo specific fact", "--repo", "repo-a"])
        result_b = runner.invoke(app, ["learn", "Default repo fact"])

        assert result_a.exit_code == 0
        assert result_b.exit_code == 0

        from llm_memory import Memory

        memory = Memory()
        repo_a = memory._storage.list_memories(repo_id="repo-a", layer="semantic")
        default_repo = memory._storage.list_memories(repo_id="default-repo", layer="semantic")

        assert any("Repo specific fact" in m["content"] for m in repo_a)
        assert not any("Default repo fact" in m["content"] for m in repo_a)
        assert any("Default repo fact" in m["content"] for m in default_repo)

    def test_stats_command(self, cli_env):
        """Test stats command (was broken with repo_id parameter)."""
        runner.invoke(app, ["init", "--type", "code"])
        runner.invoke(app, ["record", "Test memory"])

        result = runner.invoke(app, ["stats"])
        assert result.exit_code == 0
        assert "Memory Statistics" in result.output
        assert "Total Memories" in result.output

    def test_goal_command(self, cli_env):
        """Test setting a goal (was broken with repo_id parameter)."""
        runner.invoke(app, ["init", "--type", "code"])

        result = runner.invoke(app, ["goal", "Implement OAuth2", "--priority", "2"])
        assert result.exit_code == 0
        assert "Goal set" in result.output

    def test_lower_layer_cli_commands_use_configured_repo(self, cli_env):
        """Commands that call lower layers directly should still use the default repo."""
        runner.invoke(app, ["init", "--type", "code", "--repo", "default-repo"])

        bug_result = runner.invoke(app, ["bug", "Default repo bug", "--fix", "Patched it"])
        convention_result = runner.invoke(app, ["convention", "Default repo convention"])
        issue_result = runner.invoke(app, ["issue", "Default repo issue"])
        focus_result = runner.invoke(app, ["focus", "Default repo focus"])

        assert bug_result.exit_code == 0
        assert convention_result.exit_code == 0
        assert issue_result.exit_code == 0
        assert focus_result.exit_code == 0

        from llm_memory import Memory

        memory = Memory()
        scoped_memories = memory._storage.list_memories(repo_id="default-repo")
        scoped_intents = memory._storage.get_active_intents(repo_id="default-repo")

        assert any("Default repo bug" in m["content"] for m in scoped_memories)
        assert any("Default repo convention" in m["content"] for m in scoped_memories)
        assert any("Default repo issue" in m["content"] for m in scoped_memories)
        assert any("Default repo focus" in i["description"] for i in scoped_intents)

    def test_export_command(self, cli_env):
        """Test export command (was broken via stats call)."""
        runner.invoke(app, ["init", "--type", "code"])
        runner.invoke(app, ["record", "Test memory"])

        export_path = cli_env / "export.json"
        result = runner.invoke(app, ["export", str(export_path)])
        assert result.exit_code == 0
        assert export_path.exists()

        # Verify it's valid JSON
        import json

        data = json.loads(export_path.read_text())
        assert "version" in data
        assert "memories" in data

    def test_import_command_reports_missing_file(self, cli_env):
        runner.invoke(app, ["init", "--type", "code"])

        result = runner.invoke(app, ["import", str(cli_env / "missing-export.json")])

        assert result.exit_code == 1
        assert "Import failed:" in result.output
        assert "missing-export.json" in result.output
        assert "Traceback" not in result.output

    def test_import_command_reports_invalid_payload(self, cli_env):
        runner.invoke(app, ["init", "--type", "code"])
        import_path = cli_env / "invalid-export.json"
        import_path.write_text('{"memories": {"episodic": [{"metadata": {}}]}}')

        result = runner.invoke(app, ["import", str(import_path)])

        assert result.exit_code == 1
        assert "Import failed:" in result.output
        assert "memories.episodic[0].content" in result.output
        assert "Traceback" not in result.output

    def test_dedup_command(self, cli_env):
        """Test dedup command (was broken with numpy array comparison)."""
        runner.invoke(app, ["init", "--type", "code"])
        runner.invoke(app, ["record", "Test memory"])

        result = runner.invoke(app, ["dedup", "--layer", "episodic"])
        assert result.exit_code == 0
        # Should not crash - may or may not find duplicates
        assert "duplicates" in result.output.lower()

    def test_codex_hook_install_writes_agents_and_config(self, cli_env):
        """Codex hook installs project instructions and a managed MCP block."""
        config_path = cli_env / "codex" / "config.toml"

        result = runner.invoke(
            app,
            [
                "hooks",
                "install",
                "codex",
                "--server-url",
                "http://127.0.0.1:8001",
                "--repo-id",
                "repo-a",
                "--config-path",
                str(config_path),
            ],
        )

        assert result.exit_code == 0
        assert Path("AGENTS.md").exists()
        assert "Before changing code" in Path("AGENTS.md").read_text()
        config = config_path.read_text()
        assert "[mcp_servers.llm-memory]" in config
        assert 'LLM_MEMORY_STORAGE_SERVER_URL = "http://127.0.0.1:8001"' in config
        assert 'LLM_MEMORY_REPO_ID = "repo-a"' in config
        assert "LLM_MEMORY_EMBEDDING_PROVIDER" not in config

    def test_codex_hook_dry_run_does_not_write(self, cli_env):
        """Dry-run prints the Codex MCP block without mutating files."""
        config_path = cli_env / "codex" / "config.toml"

        result = runner.invoke(
            app,
            [
                "hooks",
                "install",
                "codex",
                "--config-path",
                str(config_path),
                "--dry-run",
            ],
        )

        assert result.exit_code == 0
        assert "[mcp_servers.llm-memory]" in result.output
        assert not Path("AGENTS.md").exists()
        assert not config_path.exists()

    def test_capture_git_sync_forwards_until_option(self, cli_env, monkeypatch):
        """CLI sync should expose GitCapture's supported date upper bound."""
        import llm_memory.capture.git as git_module
        import llm_memory.interfaces.cli as cli_module

        calls = {}
        memory = object()

        class FakeGitCapture:
            def __init__(self, captured_memory):
                calls["memory"] = captured_memory

            def sync_history(self, since=None, until=None, limit=100):
                calls["sync"] = {"since": since, "until": until, "limit": limit}
                return ["mem-1", "mem-2"]

        monkeypatch.setattr(cli_module, "get_memory", lambda: memory)
        monkeypatch.setattr(git_module, "GitCapture", FakeGitCapture)

        result = runner.invoke(
            app,
            [
                "capture",
                "git",
                "sync",
                "--since",
                "2024-01-01",
                "--until",
                "2024-02-01",
                "--limit",
                "7",
            ],
        )

        assert result.exit_code == 0
        assert calls["memory"] is memory
        assert calls["sync"] == {"since": "2024-01-01", "until": "2024-02-01", "limit": 7}
        assert "Captured 2 commits" in result.output


class TestCLIRecallCommand:
    """Test recall/search functionality."""

    def test_recall_basic(self, cli_env):
        """Test basic recall command."""
        runner.invoke(app, ["init", "--type", "code"])
        runner.invoke(app, ["record", "Fixed authentication bug"])

        result = runner.invoke(app, ["recall", "authentication"])
        assert result.exit_code == 0
        assert "authentication" in result.output.lower()

    def test_recall_with_layer_filter(self, cli_env):
        """Test recall with layer filter to avoid duplicates."""
        runner.invoke(app, ["init", "--type", "code"])
        runner.invoke(app, ["record", "Test episodic"])
        runner.invoke(app, ["learn", "Test semantic"])

        # Search specific layer
        result = runner.invoke(app, ["recall", "test", "--layer", "episodic"])
        assert result.exit_code == 0
        # Should not show duplicates
        lines = [line for line in result.output.split("\n") if "episodic" in line.lower()]
        # Count result rows (should be 1 for episodic)
        assert len(lines) >= 1

    def test_recall_similarity_scores(self, cli_env):
        """Test that similarity scores are reasonable (not negative)."""
        runner.invoke(app, ["init", "--type", "code"])
        runner.invoke(app, ["record", "Fixed authentication bug in login"])
        runner.invoke(app, ["record", "Implemented user profile page"])

        result = runner.invoke(app, ["recall", "authentication", "--layer", "episodic"])
        assert result.exit_code == 0

        # Check that scores are present and not negative
        # The output should have Score column with values
        assert "Score" in result.output
        # Should not have negative scores (which were the bug)
        assert "-0." not in result.output or result.output.count("-0.") == 0

    def test_remember_returns_latest_memory(self, cli_env):
        """Remember should show the newest active memory."""
        runner.invoke(app, ["init", "--type", "code"])
        runner.invoke(app, ["record", "Older CLI memory"])
        runner.invoke(app, ["record", "Latest CLI memory"])

        result = runner.invoke(app, ["remember"])

        assert result.exit_code == 0
        assert "Latest Memory" in result.output
        assert "Latest CLI memory" in result.output


class TestCLIListCommands:
    """Test various list commands."""

    def test_list_memories(self, cli_env):
        """Test listing memories."""
        runner.invoke(app, ["init", "--type", "code"])
        runner.invoke(app, ["record", "Test memory"])

        result = runner.invoke(app, ["list", "--layer", "episodic"])
        assert result.exit_code == 0
        assert "Test memory" in result.output

    def test_list_warnings(self, cli_env):
        """Test listing warnings."""
        runner.invoke(app, ["init", "--type", "code"])
        runner.invoke(app, ["warn", "auth.py", "Watch for race conditions"])

        result = runner.invoke(app, ["list-warnings"])
        assert result.exit_code == 0
        assert "race conditions" in result.output.lower()

    def test_list_intents(self, cli_env):
        """Test listing intents."""
        runner.invoke(app, ["init", "--type", "code"])
        runner.invoke(app, ["goal", "Implement feature X"])

        result = runner.invoke(app, ["list-intents"])
        assert result.exit_code == 0
        # Check that the goal appears in output (case-insensitive)
        assert "implement feature x" in result.output.lower()

    def test_intent_subcommands_update_close_and_list_by_status(self, cli_env):
        """Intent subcommands should update and close intents by ID."""
        runner.invoke(app, ["init", "--type", "code"])
        runner.invoke(app, ["goal", "Original feature goal", "--priority", "1"])

        from llm_memory import Memory

        memory = Memory()
        intent_id = memory.intent.get_active()[0]["id"]

        update = runner.invoke(
            app,
            [
                "intent",
                "update",
                intent_id,
                "--description",
                "Updated feature goal",
                "--priority",
                "3",
            ],
        )
        close = runner.invoke(app, ["intent", "close", intent_id])
        closed = runner.invoke(app, ["intent", "list", "--status", "closed"])
        active = runner.invoke(app, ["list-intents"])

        assert update.exit_code == 0
        assert close.exit_code == 0
        assert closed.exit_code == 0
        assert active.exit_code == 0
        assert "Updated feature goal" in closed.output
        assert "CRITICAL" in closed.output
        assert "Updated feature goal" not in active.output

    def test_intent_complete_subcommand(self, cli_env):
        """Intent complete should mark a specific intent completed."""
        runner.invoke(app, ["init", "--type", "code"])
        runner.invoke(app, ["goal", "Complete this goal"])

        from llm_memory import Memory

        memory = Memory()
        intent_id = memory.intent.get_active()[0]["id"]

        complete = runner.invoke(app, ["intent", "complete", intent_id])
        completed = runner.invoke(app, ["list-intents", "--status", "completed"])

        assert complete.exit_code == 0
        assert completed.exit_code == 0
        assert "Complete this goal" in completed.output

    def test_list_commands_use_configured_repo(self, cli_env):
        """List commands should not show records from other repos by default."""
        runner.invoke(app, ["init", "--type", "code", "--repo", "repo-a"])
        runner.invoke(app, ["record", "Repo A event"])
        runner.invoke(app, ["record", "Repo B event", "--repo", "repo-b"])
        runner.invoke(app, ["goal", "Repo A goal"])
        runner.invoke(app, ["goal", "Repo B goal", "--repo", "repo-b"])
        runner.invoke(app, ["warn", "auth.py", "Repo A warning"])
        runner.invoke(app, ["warn", "auth.py", "Repo B warning", "--repo", "repo-b"])
        runner.invoke(app, ["issue", "Repo A issue"])
        runner.invoke(app, ["issue", "Repo B issue", "--repo", "repo-b"])

        memories = runner.invoke(app, ["list", "--full"])
        intents = runner.invoke(app, ["list-intents"])
        warnings = runner.invoke(app, ["list-warnings"])
        issues = runner.invoke(app, ["list-issues"])

        assert memories.exit_code == 0
        assert intents.exit_code == 0
        assert warnings.exit_code == 0
        assert issues.exit_code == 0

        assert "Repo A event" in memories.output
        assert "Repo B event" not in memories.output
        assert "Repo A goal" in intents.output
        assert "Repo B goal" not in intents.output
        assert "Repo A warning" in warnings.output
        assert "Repo B warning" not in warnings.output
        assert "Repo A issue" in issues.output
        assert "Repo B issue" not in issues.output

    def test_decay_preview_commands_show_risk_without_mutating_importance(self, cli_env):
        """Decay preview should identify old memories without applying decay."""
        runner.invoke(app, ["init", "--type", "code"])

        from llm_memory import Memory

        memory = Memory()
        memory_id = memory.record("Old unused memory for decay preview", importance=0.8)
        with memory._storage._get_db() as conn:
            conn.execute(
                "UPDATE memories SET accessed_at = '2020-01-01 00:00:00' WHERE id = ?",
                (memory_id,),
            )
            conn.commit()

        top_level = runner.invoke(app, ["decay-preview", "--halflife-days", "30"])
        grouped = runner.invoke(app, ["health", "decay-preview", "--halflife-days", "30"])
        stored = memory._storage.list_memories(status="active", limit=1)[0]

        assert top_level.exit_code == 0
        assert grouped.exit_code == 0
        assert "likely_to_decay" in top_level.output
        assert "Old unused memory" in top_level.output
        assert "Memory Health" in grouped.output
        assert stored["importance"] == 0.8


class TestCLIContextGeneration:
    """Test context generation commands."""

    def test_context_command(self, cli_env):
        """Test context generation."""
        runner.invoke(app, ["init", "--type", "code"])
        runner.invoke(app, ["record", "Test event"])
        runner.invoke(app, ["warn", "test.py", "Be careful"])

        result = runner.invoke(app, ["context"])
        assert result.exit_code == 0
        assert "Project Memory Context" in result.output

    def test_inject_command(self, cli_env):
        """Test file-based context injection."""
        runner.invoke(app, ["init", "--type", "code"])
        runner.invoke(app, ["warn", "auth/token.py", "Race condition possible"])
        runner.invoke(app, ["record", "Fixed auth bug"])

        result = runner.invoke(app, ["inject", "--file", "auth/token.py"])
        assert result.exit_code == 0
        # Should show warnings for the file
        assert "Race condition" in result.output or "race condition" in result.output.lower()


class TestCLIWorkingAndDone:
    """Test task management commands."""

    def test_working_command(self, cli_env):
        """Test setting current task."""
        runner.invoke(app, ["init", "--type", "code"])

        result = runner.invoke(app, ["working", "Refactoring auth module"])
        assert result.exit_code == 0
        assert "Current task set" in result.output or "Working on" in result.output

    def test_done_command(self, cli_env):
        """Test clearing current task."""
        runner.invoke(app, ["init", "--type", "code"])
        runner.invoke(app, ["working", "Some task"])

        result = runner.invoke(app, ["done"])
        assert result.exit_code == 0


class TestCLIEdgeCases:
    """Test edge cases and error handling."""

    def test_init_without_overwrite(self, cli_env):
        """Test that init doesn't overwrite existing config."""
        runner.invoke(app, ["init", "--type", "code"])

        # Try to init again
        result = runner.invoke(app, ["init", "--type", "code"])
        assert result.exit_code != 0
        assert "already exists" in result.output.lower()

    def test_init_force_overwrites_existing_config(self, cli_env):
        """--force allows init to replace an existing config file."""
        runner.invoke(app, ["init", "--type", "code", "--repo", "old-repo"])

        result = runner.invoke(
            app,
            ["init", "--type", "writing", "--repo", "new-repo", "--force"],
        )

        assert result.exit_code == 0
        assert "Initialized LLM Memory" in result.output

        from llm_memory.config import MemoryConfig

        config = MemoryConfig.from_file(Path("llm-memory.yaml"))
        assert config.project_type == "writing"
        assert config.repo_id == "new-repo"

    def test_recall_with_no_results(self, cli_env):
        """Test recall when no results found."""
        runner.invoke(app, ["init", "--type", "code"])

        result = runner.invoke(app, ["recall", "nonexistent_query_xyz"])
        assert result.exit_code == 0
        assert "No results" in result.output or "0" in result.output

    def test_stats_on_empty_database(self, cli_env):
        """Test stats on empty database."""
        runner.invoke(app, ["init", "--type", "code"])

        result = runner.invoke(app, ["stats"])
        assert result.exit_code == 0
        assert "Total Memories" in result.output


class TestCLIDiagnostics:
    """Test CLI diagnostics commands."""

    def test_doctor_command_json(self, cli_env):
        """Doctor should emit structured diagnostics JSON."""
        runner.invoke(app, ["init", "--type", "code"])

        result = runner.invoke(app, ["doctor", "--format", "json"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["configured"]["project_type"] == "code"
        assert payload["storage"]["mode"] in ("local", "client")
        assert payload["providers"]["embedding"]["configured_provider"] == "noop"

    def test_doctor_command_text(self, cli_env):
        """Doctor text mode should emit a readable summary."""
        runner.invoke(app, ["init", "--type", "code"])

        result = runner.invoke(app, ["doctor"])
        assert result.exit_code == 0
        assert "LLM Memory Diagnostics" in result.output

    def test_providers_command_json_is_safe_for_secrets(self, cli_env, monkeypatch):
        """Providers JSON should not print secret values, only presence flags."""
        runner.invoke(app, ["init", "--type", "code"])
        monkeypatch.setenv("EMBEDDING_API_KEY", "secret-token")

        result = runner.invoke(app, ["providers", "--format", "json"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["embedding"]["credentials"]["api_key_configured"] is True
        assert "secret-token" not in result.output

    def test_providers_test_command_uses_connectivity(self, cli_env, monkeypatch):
        """Provider test should run connectivity check for explicit provider."""
        runner.invoke(app, ["init", "--type", "code"])

        def fake_provider(_config, verify: bool = True):
            raise RuntimeError("provider request rejected")

        monkeypatch.setattr("llm_memory.core.embeddings.get_embedding_provider", fake_provider)

        result = runner.invoke(app, ["providers", "test", "openai", "--format", "json"])
        assert result.exit_code == 1
        payload = json.loads(result.output)
        assert payload["result"]["configured_provider"] == "openai"
        assert payload["result"]["connected"] is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
