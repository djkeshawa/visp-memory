"""Tests for automatic memory injection via real Claude Code hooks."""

import json
import tempfile
from pathlib import Path

import pytest

from llm_memory import Memory, MemoryConfig
from llm_memory.hooks.claude_code_auto import (
    PRE_TOOL_USE_MATCHER,
    handle_pre_tool_use,
    handle_session_start,
    install_auto_inject_hooks,
    uninstall_auto_inject_hooks,
)


@pytest.fixture
def memory():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        config = MemoryConfig()
        config.storage.data_dir = Path(tmpdir)
        config.embedding.provider = "noop"
        yield Memory(config=config)


class TestSessionStartHandler:
    def test_injects_project_memory_context(self, memory):
        memory.goal("Ship the auth rewrite", priority=2)
        memory.warn("auth.py", "Race condition in token refresh")

        output = handle_session_start({"session_id": "s1"}, memory=memory)

        assert output is not None
        specific = output["hookSpecificOutput"]
        assert specific["hookEventName"] == "SessionStart"
        assert "Ship the auth rewrite" in specific["additionalContext"]
        assert "Race condition" in specific["additionalContext"]

    def test_never_sets_permission_decision(self, memory):
        memory.goal("Some goal")
        output = handle_session_start({}, memory=memory)
        assert "permissionDecision" not in json.dumps(output)

    def test_fails_open_when_memory_unavailable(self):
        class Broken:
            def context(self, **kwargs):
                raise RuntimeError("storage down")

            config = None

        assert handle_session_start({}, memory=Broken()) is None


class TestPreToolUseHandler:
    def test_injects_file_warnings_before_read(self, memory):
        memory.warn("src/auth.py", "Mutex required around token refresh")

        output = handle_pre_tool_use(
            {
                "session_id": "s1",
                "tool_name": "Read",
                "tool_input": {"file_path": "src/auth.py"},
            },
            memory=memory,
        )

        assert output is not None
        specific = output["hookSpecificOutput"]
        assert specific["hookEventName"] == "PreToolUse"
        assert "Mutex required" in specific["additionalContext"]
        assert "permissionDecision" not in specific

    def test_same_file_injected_once_per_session(self, memory):
        memory.warn("src/auth.py", "Mutex required around token refresh")
        payload = {
            "session_id": "s2",
            "tool_name": "Edit",
            "tool_input": {"file_path": "src/auth.py"},
        }

        assert handle_pre_tool_use(payload, memory=memory) is not None
        assert handle_pre_tool_use(payload, memory=memory) is None

    def test_different_sessions_do_not_share_state(self, memory):
        memory.warn("src/auth.py", "Mutex required around token refresh")
        base = {"tool_name": "Read", "tool_input": {"file_path": "src/auth.py"}}

        assert handle_pre_tool_use({**base, "session_id": "a"}, memory=memory) is not None
        assert handle_pre_tool_use({**base, "session_id": "b"}, memory=memory) is not None

    def test_no_output_for_unknown_file(self, memory):
        output = handle_pre_tool_use(
            {
                "session_id": "s3",
                "tool_name": "Read",
                "tool_input": {"file_path": "docs/nothing_known.md"},
            },
            memory=memory,
        )
        assert output is None

    def test_no_output_without_file_path(self, memory):
        assert (
            handle_pre_tool_use(
                {"session_id": "s4", "tool_name": "Bash", "tool_input": {"command": "ls"}},
                memory=memory,
            )
            is None
        )

    def test_fails_open_on_malformed_payload(self, memory):
        assert handle_pre_tool_use({"tool_input": "not-a-dict"}, memory=memory) is None


class TestInstaller:
    def test_installs_both_hooks(self, tmp_path):
        settings_path = install_auto_inject_hooks(tmp_path)

        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        assert "SessionStart" in settings["hooks"]
        assert "PreToolUse" in settings["hooks"]
        pre = settings["hooks"]["PreToolUse"][0]
        assert pre["matcher"] == PRE_TOOL_USE_MATCHER
        assert pre["hooks"][0]["command"] == "llm-memory hook pre-tool-use"

    def test_install_is_idempotent(self, tmp_path):
        install_auto_inject_hooks(tmp_path)
        install_auto_inject_hooks(tmp_path)

        settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        assert len(settings["hooks"]["SessionStart"]) == 1
        assert len(settings["hooks"]["PreToolUse"]) == 1

    def test_install_preserves_existing_settings(self, tmp_path):
        settings_path = tmp_path / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True)
        settings_path.write_text(
            json.dumps(
                {
                    "permissions": {"allow": ["Bash(ls:*)"]},
                    "hooks": {
                        "PreToolUse": [
                            {
                                "matcher": "Bash",
                                "hooks": [{"type": "command", "command": "custom-guard"}],
                            }
                        ]
                    },
                }
            ),
            encoding="utf-8",
        )

        install_auto_inject_hooks(tmp_path)

        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        assert settings["permissions"] == {"allow": ["Bash(ls:*)"]}
        commands = [
            hook["command"]
            for entry in settings["hooks"]["PreToolUse"]
            for hook in entry["hooks"]
        ]
        assert "custom-guard" in commands
        assert "llm-memory hook pre-tool-use" in commands

    def test_install_refuses_invalid_json(self, tmp_path):
        settings_path = tmp_path / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True)
        settings_path.write_text("{not json", encoding="utf-8")

        with pytest.raises(ValueError, match="not valid JSON"):
            install_auto_inject_hooks(tmp_path)

    def test_uninstall_removes_only_ours(self, tmp_path):
        settings_path = tmp_path / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True)
        settings_path.write_text(
            json.dumps(
                {
                    "hooks": {
                        "PreToolUse": [
                            {
                                "matcher": "Bash",
                                "hooks": [{"type": "command", "command": "custom-guard"}],
                            }
                        ]
                    }
                }
            ),
            encoding="utf-8",
        )
        install_auto_inject_hooks(tmp_path)

        assert uninstall_auto_inject_hooks(tmp_path) is True

        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        assert "SessionStart" not in settings.get("hooks", {})
        commands = [
            hook["command"]
            for entry in settings["hooks"]["PreToolUse"]
            for hook in entry["hooks"]
        ]
        assert commands == ["custom-guard"]

    def test_uninstall_missing_file_is_noop(self, tmp_path):
        assert uninstall_auto_inject_hooks(tmp_path) is False
