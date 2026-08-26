"""Behavior-focused coverage for CLI failure paths and integration boundaries."""

from pathlib import Path
from types import SimpleNamespace

from typer.testing import CliRunner

from visp_memory.interfaces.cli import app

runner = CliRunner()


def test_write_without_repository_scope_explains_the_repair(cli_env):
    result = runner.invoke(app, ["record", "unscoped event"])

    assert result.exit_code == 1
    assert "No repository scope is configured" in result.output
    assert "visp-memory init" in result.output
    assert "Traceback" not in result.output


def test_empty_listing_commands_are_explicit(cli_env):
    runner.invoke(app, ["init", "--type", "code"])

    assert "No memories found" in runner.invoke(app, ["list"]).output
    assert "No memories found" in runner.invoke(app, ["remember"]).output
    assert "No warnings" in runner.invoke(app, ["list-warnings"]).output
    assert "No known issues" in runner.invoke(app, ["list-issues"]).output
    assert "No active intents" in runner.invoke(app, ["list-intents"]).output


def test_intent_commands_report_validation_not_found_and_missing_fields(cli_env):
    runner.invoke(app, ["init", "--type", "code"])

    no_fields = runner.invoke(app, ["intent", "update", "missing"])
    bad_status = runner.invoke(app, ["intent", "update", "missing", "--status", "pending"])
    update_missing = runner.invoke(
        app, ["intent", "update", "missing", "--description", "new description"]
    )
    complete_missing = runner.invoke(app, ["intent", "complete", "missing"])
    close_missing = runner.invoke(app, ["intent", "close", "missing"])

    assert no_fields.exit_code == 1
    assert "No fields provided" in no_fields.output
    assert bad_status.exit_code != 0
    assert "status must be active" in bad_status.output
    assert update_missing.exit_code == complete_missing.exit_code == close_missing.exit_code == 1
    assert "Intent not found" in update_missing.output
    assert "Intent not found" in complete_missing.output
    assert "Intent not found" in close_missing.output


def test_feedback_commands_cover_text_empty_and_confirmation_paths(cli_env):
    runner.invoke(app, ["init", "--type", "code"])
    record = runner.invoke(app, ["record", "feedback target"])
    memory_id = record.output.split("ID:", 1)[1].strip().splitlines()[0]

    no_confirmation = runner.invoke(
        app, ["feedback", "reset", "--memory-id", memory_id]
    )
    log = runner.invoke(
        app, ["feedback", "log", "--memory-id", memory_id, "--event", "used"]
    )
    inspect = runner.invoke(app, ["feedback", "inspect"])
    reset = runner.invoke(
        app, ["feedback", "reset", "--memory-id", memory_id, "--yes"]
    )

    assert no_confirmation.exit_code == 1
    assert "Pass --yes" in no_confirmation.output
    assert log.exit_code == 0
    assert "Recorded feedback" in log.output
    assert inspect.exit_code == 0
    assert "Recall Utility Signals" in inspect.output
    assert "used=1" in inspect.output
    assert reset.exit_code == 0
    assert "Deleted 1 feedback events" in reset.output


def test_quality_conflict_command_reports_clean_conflict_and_error(monkeypatch, cli_env):
    import visp_memory.interfaces.cli as cli_module

    class FakeMemory:
        def __init__(self, result=None, error=None):
            self.result = result
            self.error = error

        def check_conflict(self, content, *, layer):
            assert (content, layer) == ("new statement", "semantic")
            if self.error:
                raise self.error
            return self.result

    monkeypatch.setattr(cli_module, "get_memory", lambda: FakeMemory())
    clean = runner.invoke(app, ["quality", "conflicts", "new statement"])
    assert clean.exit_code == 0
    assert "No conflicts detected" in clean.output

    monkeypatch.setattr(
        cli_module,
        "get_memory",
        lambda: FakeMemory({"reason": "contradicts prior fact", "conflicting_ids": ["m1"]}),
    )
    conflict = runner.invoke(app, ["quality", "conflicts", "new statement"])
    assert conflict.exit_code == 0
    assert "Conflict Detected" in conflict.output
    assert "m1" in conflict.output

    monkeypatch.setattr(cli_module, "get_memory", lambda: FakeMemory(error=RuntimeError("down")))
    failed = runner.invoke(app, ["quality", "conflicts", "new statement"])
    assert failed.exit_code == 1
    assert "Error checking conflicts" in failed.output


def test_decay_preview_has_empty_and_ranked_output_paths(monkeypatch, cli_env):
    import visp_memory.interfaces.cli as cli_module

    class Storage:
        def __init__(self, rows):
            self.rows = rows

        def list_memories(self, **kwargs):
            assert kwargs["status"] == "active"
            return self.rows

    config = SimpleNamespace(decay_halflife_days=30, decay_enabled=True, repo_id="repo-a")
    memory = SimpleNamespace(config=config, _storage=Storage([]))
    monkeypatch.setattr(cli_module, "get_memory", lambda: memory)
    empty = runner.invoke(app, ["decay-preview"])
    assert empty.exit_code == 0
    assert "No active memories found" in empty.output

    memory._storage.rows = [
        {
            "id": "floor",
            "importance": 0.1,
            "accessed_at": "2020-01-01T00:00:00+00:00",
            "access_count": 0,
            "layer": "semantic",
            "category": "fact",
            "content": "at floor",
        },
        {
            "id": "weak",
            "importance": 0.8,
            "accessed_at": "2026-01-01T00:00:00+00:00",
            "access_count": 0,
            "layer": "episodic",
            "category": "note",
            "content": "weak memory",
        },
    ]
    ranked = runner.invoke(app, ["decay-preview", "--limit", "2", "--halflife-days", "30"])
    assert ranked.exit_code == 0
    assert "Memory decay preview" in ranked.output
    assert "at_floor" in ranked.output
    assert "weak" in ranked.output


def test_status_dashboard_renders_activity_and_current_context(cli_env):
    runner.invoke(app, ["init", "--type", "code"])
    runner.invoke(app, ["record", "Dashboard event"])
    runner.invoke(app, ["goal", "Dashboard goal"])
    runner.invoke(app, ["focus", "Dashboard focus"])
    runner.invoke(app, ["working", "Dashboard task"])

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "Visp Memory System" in result.output
    assert "Dashboard event" in result.output
    assert "Dashboard focus" in result.output
    assert "Dashboard task" in result.output


def test_capture_git_cli_dispatches_all_actions(monkeypatch, cli_env):
    import visp_memory.capture.git as git_module
    import visp_memory.interfaces.cli as cli_module

    calls = []

    class FakeGitCapture:
        def __init__(self, memory):
            calls.append(("init", memory))

        def install_hooks(self):
            calls.append("install")
            return {"post-commit": True, "post-merge": False}

        def uninstall_hooks(self):
            calls.append("uninstall")
            return {"post-commit": True, "post-merge": False}

        def sync_history(self, **kwargs):
            calls.append(("sync", kwargs))
            return ["m1"]

        def on_commit(self, ref):
            calls.append(("commit", ref))
            return "m2"

        def on_merge(self):
            calls.append("merge")
            return None

    memory = object()
    monkeypatch.setattr(cli_module, "get_memory", lambda: memory)
    monkeypatch.setattr(git_module, "GitCapture", FakeGitCapture)

    install = runner.invoke(app, ["capture", "git", "install"])
    uninstall = runner.invoke(app, ["capture", "git", "uninstall"])
    sync = runner.invoke(app, ["capture", "git", "sync", "--limit", "3"])
    commit = runner.invoke(app, ["capture", "git", "commit", "--ref", "abc"])
    merge = runner.invoke(app, ["capture", "git", "merge"])
    unknown = runner.invoke(app, ["capture", "git", "wat"])

    assert install.exit_code == uninstall.exit_code == sync.exit_code == commit.exit_code == 0
    assert merge.exit_code == 0
    assert unknown.exit_code == 1
    assert "Failed to install post-merge" in install.output
    assert "Captured 1 commits" in sync.output
    assert "Captured commit" in commit.output
    assert "Not a merge commit" in merge.output
    assert "Unknown action" in unknown.output
    assert ("sync", {"since": None, "until": None, "limit": 3}) in calls
    assert ("commit", "abc") in calls


def test_capture_git_cli_handles_missing_repository(monkeypatch, cli_env):
    import visp_memory.capture.git as git_module
    import visp_memory.interfaces.cli as cli_module

    monkeypatch.setattr(cli_module, "get_memory", lambda: object())

    class BrokenGitCapture:
        def __init__(self, memory):
            raise ValueError("Not a git repository: project")

    monkeypatch.setattr(git_module, "GitCapture", BrokenGitCapture)
    result = runner.invoke(app, ["capture", "git", "commit"])
    assert result.exit_code == 1
    assert "Not a git repository" in result.output


def test_capture_tests_cli_reports_success_and_parser_error(monkeypatch, cli_env, tmp_path):
    import visp_memory.capture.tests as capture_module
    import visp_memory.interfaces.cli as cli_module

    class FakeCapture:
        def __init__(self, memory):
            pass

        def on_pytest_session(self, report):
            if report == "bad.xml":
                raise ValueError("invalid report")
            return ["m1", "m2"]

    monkeypatch.setattr(cli_module, "get_memory", lambda: object())
    monkeypatch.setattr(capture_module, "TestCapture", FakeCapture)
    good = runner.invoke(app, ["capture", "tests", "good.xml"])
    bad = runner.invoke(app, ["capture", "tests", "bad.xml"])
    assert good.exit_code == 0
    assert "Captured 2 test failures" in good.output
    assert bad.exit_code == 1
    assert "Error parsing report" in bad.output


def test_capture_conversation_cli_formats_dry_run_and_configuration_error(
    monkeypatch, cli_env, tmp_path
):
    import visp_memory.capture.conversation as conversation_module
    import visp_memory.interfaces.cli as cli_module

    class FakeCapture:
        def __init__(self, memory):
            pass

        def parse_file(self, path, dry_run=False):
            assert Path(path).name == "chat.md"
            return {
                "decisions": 1,
                "learnings": 2,
                "bugs": 0,
                "tasks": 1,
                "raw": {"source": "safe"},
            }

    monkeypatch.setattr(cli_module, "get_memory", lambda: object())
    monkeypatch.setattr(conversation_module, "ConversationCapture", FakeCapture)
    path = tmp_path / "chat.md"
    path.write_text("conversation", encoding="utf-8")
    result = runner.invoke(app, ["capture", "conversation", str(path), "--dry-run"])
    assert result.exit_code == 0
    assert "Extraction Results (Dry Run)" in result.output
    assert "Raw Extraction" in result.output

    class BrokenCapture:
        def __init__(self, memory):
            raise ValueError("No LLM provider configured")

    monkeypatch.setattr(conversation_module, "ConversationCapture", BrokenCapture)
    failed = runner.invoke(app, ["capture", "conversation", str(path)])
    assert failed.exit_code == 1
    assert "Configuration Error" in failed.output
    assert "VISP_MEMORY_CAPTURE_LLM_PROVIDER" in failed.output


def test_hooks_install_cli_reports_preview_and_os_error(monkeypatch, cli_env):
    import visp_memory.interfaces.cli as cli_module

    class Adapter:
        config_path = Path("codex.toml")

        def install(self):
            return {"instructions": True, "config": False}

        def get_context_file_path(self):
            return Path("AGENTS.md")

        def config_block(self):
            return "[mcp_servers.visp-memory]\n"

    monkeypatch.setattr(cli_module, "get_memory", lambda: object())
    monkeypatch.setattr(cli_module, "_get_hook_adapter", lambda *args, **kwargs: Adapter())
    preview = runner.invoke(app, ["hooks", "install", "codex", "--dry-run"])
    assert preview.exit_code == 0
    assert "Previewing codex integration" in preview.output
    assert "Managed MCP config block" in preview.output
    assert "config" in preview.output

    class BrokenAdapter(Adapter):
        def install(self):
            raise OSError("permission denied")

    monkeypatch.setattr(cli_module, "_get_hook_adapter", lambda *args, **kwargs: BrokenAdapter())
    failed = runner.invoke(app, ["hooks", "install", "generic"])
    assert failed.exit_code == 1
    assert "failed to install generic integration" in failed.output


def test_hooks_update_cli_handles_uninstalled_failure_and_success(monkeypatch, cli_env):
    import visp_memory.hooks as hooks_module
    import visp_memory.interfaces.cli as cli_module

    class Adapter:
        def __init__(self, installed=True, result=True):
            self.installed = installed
            self.result = result

        def is_installed(self):
            return self.installed

        def update_context(self, **kwargs):
            return self.result

        def get_context_file_path(self):
            return Path("context.md")

    monkeypatch.setattr(cli_module, "get_memory", lambda: object())
    monkeypatch.setattr(hooks_module, "get_adapter", lambda *args, **kwargs: Adapter(False))
    absent = runner.invoke(app, ["hooks", "update", "generic"])
    assert absent.exit_code == 1
    assert "not installed" in absent.output

    monkeypatch.setattr(hooks_module, "get_adapter", lambda *args, **kwargs: Adapter(result=False))
    failed = runner.invoke(app, ["hooks", "update", "generic"])
    assert failed.exit_code == 1
    assert "Failed to update context" in failed.output

    monkeypatch.setattr(hooks_module, "get_adapter", lambda *args, **kwargs: Adapter())
    updated = runner.invoke(
        app, ["hooks", "update", "generic", "--file", "src/a.py", "--task", "fix"]
    )
    assert updated.exit_code == 0
    assert "Context updated" in updated.output


def test_hooks_list_and_uninstall_cli_are_user_visible(monkeypatch, cli_env):
    import visp_memory.interfaces.cli as cli_module

    class Adapter:
        config_path = Path("codex.toml")

        def uninstall(self):
            return {"instructions": True, "config": False}

        def get_context_file_path(self):
            return Path("context.md")

    monkeypatch.setattr(cli_module, "get_memory", lambda: object())
    monkeypatch.setattr(cli_module, "_get_hook_adapter", lambda *args, **kwargs: Adapter())
    listed = runner.invoke(app, ["hooks", "list"])
    removed = runner.invoke(app, ["hooks", "uninstall", "codex", "--dry-run"])
    assert listed.exit_code == 0
    assert "Available LLM Tool Integrations" in listed.output
    assert "claude-code" in listed.output
    assert removed.exit_code == 0
    assert "Previewing removal of codex integration" in removed.output


def test_serve_command_starts_uvicorn_after_auth_preflight(monkeypatch, cli_env):
    import sys
    import types

    import visp_memory.interfaces.cli as cli_module

    config = SimpleNamespace(
        server=SimpleNamespace(
            auth_enabled=False,
            jwt_secret="",
            api_keys=[],
            allow_anonymous=False,
        ),
        storage=SimpleNamespace(api_key=None),
    )
    calls = []
    uvicorn = types.SimpleNamespace(run=lambda *args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setattr(cli_module, "load_config", lambda: config)
    monkeypatch.setitem(sys.modules, "uvicorn", uvicorn)

    result = runner.invoke(app, ["serve", "--host", "127.0.0.1", "--port", "8123"])
    assert result.exit_code == 0
    assert "Starting Central Memory Server" in result.output
    assert calls == [
        (("visp_memory.server.app:app",), {"host": "127.0.0.1", "port": 8123, "reload": False})
    ]


def test_contract_propose_reports_failed_quarantine_and_cleans_up(monkeypatch, cli_env):
    import visp_memory.interfaces.cli as cli_module

    class Storage:
        def __init__(self):
            self.deleted = []

        def update_memory(self, memory_id, **kwargs):
            return False

        def delete_memory(self, memory_id):
            self.deleted.append(memory_id)

    storage = Storage()
    memory = SimpleNamespace(
        config=SimpleNamespace(repo_id="repo-a"),
        _storage=storage,
        record=lambda *args, **kwargs: "proposal-1",
    )
    monkeypatch.setattr(cli_module, "get_memory", lambda: memory)
    result = runner.invoke(app, ["contract", "propose", "unsafe proposal"])

    assert result.exit_code == 1
    assert "could not be quarantined" in result.output
    assert storage.deleted == ["proposal-1"]
