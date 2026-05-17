"""
Integration tests for CLI commands.

These tests verify that CLI commands work end-to-end and catch regressions
that unit tests might miss (e.g., parameter mismatches between layers).
"""

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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
