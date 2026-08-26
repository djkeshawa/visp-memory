"""Integration-boundary tests for the file and runtime hook adapters."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from visp_memory.hooks import get_adapter
from visp_memory.hooks.aider import AiderAdapter
from visp_memory.hooks.claude_code import ClaudeCodeAdapter
from visp_memory.hooks.claude_code_auto import (
    handle_pre_tool_use,
    handle_session_start,
    install_auto_inject_hooks,
    uninstall_auto_inject_hooks,
)
from visp_memory.hooks.codex import CodexAdapter
from visp_memory.hooks.cursor import CursorAdapter
from visp_memory.hooks.generic import GenericAdapter


def _memory(repo_id="hook-repo", data_dir=None, context=None):
    return SimpleNamespace(
        config=SimpleNamespace(
            repo_id=repo_id,
            storage=SimpleNamespace(data_dir=data_dir or Path(".hook-store")),
        ),
        context=context or (lambda **_: "full context"),
    )


def _stub_context(adapter, value="STUBBED CONTEXT"):
    adapter.get_memory_context = lambda files=None, task=None, format="markdown": value
    return adapter


def test_base_context_selection_uses_the_proactive_recall_contract(monkeypatch, tmp_path):
    calls = []

    class FakeRecall:
        def __init__(self, memory):
            calls.append(("init", memory))

        def find_relevant_for_task(self, task, files=None):
            calls.append(("task", task, files))
            return "task result"

        def for_files(self, files):
            calls.append(("files", files))
            return "file result"

        def format_injection(self, context, format="markdown"):
            calls.append(("format", context, format))
            return f"{format}:{context}"

    monkeypatch.setattr("visp_memory.recall.proactive.ProactiveRecall", FakeRecall)
    memory = _memory(context=lambda **_: "full result")
    adapter = GenericAdapter(memory, project_root=tmp_path)

    assert adapter.get_memory_context(files=["a.py"], task="fix") == "markdown:task result"
    assert adapter.get_memory_context(files=["a.py"]) == "markdown:file result"
    assert adapter.get_memory_context(task="fix") == "markdown:task result"
    assert adapter.get_memory_context() == "full result"
    assert [call for call in calls if call[0] != "init"] == [
        ("task", "fix", ["a.py"]),
        ("format", "task result", "markdown"),
        ("files", ["a.py"]),
        ("format", "file result", "markdown"),
        ("task", "fix", None),
        ("format", "task result", "markdown"),
    ]


def test_base_install_update_uninstall_preserves_standalone_file(tmp_path):
    target = tmp_path / "nested" / "context.md"
    adapter = _stub_context(
        GenericAdapter(_memory(), project_root=tmp_path, context_file="nested/context.md")
    )
    assert adapter.is_installed() is False
    assert adapter.install() == {"context_file": True}
    assert adapter.is_installed() is True
    assert adapter.update_context(task="standalone context") is True
    before_uninstall = target.read_text(encoding="utf-8")

    assert adapter.uninstall() == {"context_file": True}
    assert adapter.is_installed() is False
    backup = target.with_suffix(".md.backup")
    assert backup.read_text(encoding="utf-8") == before_uninstall


def test_aider_install_update_and_uninstall_preserve_starter_contract(tmp_path, monkeypatch):
    adapter = _stub_context(AiderAdapter(_memory(), project_root=tmp_path))
    installed = adapter.install()
    assert installed["aider_file_created"] is True
    target = tmp_path / ".aider"
    starter = target.read_text(encoding="utf-8")
    assert "visp-memory goal" in starter
    assert "visp-memory record" in starter

    class FixedClock:
        @staticmethod
        def strftime(_format):
            return "2026-01-02 03:04:05"

    monkeypatch.setattr("visp_memory.hooks.aider.utc_now", lambda: FixedClock())
    assert adapter.update_context(files=["src/a.py"], task="test") is True
    updated = target.read_text(encoding="utf-8")
    assert "STUBBED CONTEXT" in updated
    assert "2026-01-02 03:04:05" in updated

    removed = adapter.uninstall()
    assert removed == {"context_file": True}
    assert not target.exists()
    assert (tmp_path / ".aider.backup").exists()


def test_cursor_update_and_uninstall_keep_markers_well_formed(tmp_path):
    adapter = _stub_context(CursorAdapter(_memory(), project_root=tmp_path))
    adapter.install()
    assert adapter.update_context(task="cursor task") is True
    target = tmp_path / ".cursorrules"
    content = target.read_text(encoding="utf-8")
    assert "STUBBED CONTEXT" in content
    assert content.count("# LLM-MEMORY START") == 1
    assert content.count("# LLM-MEMORY END") == 1

    assert adapter.uninstall() == {"injection_removed": True}
    remaining = target.read_text(encoding="utf-8")
    assert "# Cursor Rules" in remaining
    assert "# LLM-MEMORY START" not in remaining


def test_claude_update_installs_missing_file_before_replacing_context(tmp_path):
    adapter = _stub_context(ClaudeCodeAdapter(_memory(), project_root=tmp_path))
    assert adapter.update_context(task="first update") is True
    target = tmp_path / "CLAUDE.md"
    assert target.exists()
    assert "STUBBED CONTEXT" in target.read_text(encoding="utf-8")


def test_codex_install_update_uninstall_and_existing_file_backup(tmp_path):
    config_path = tmp_path / "codex" / "config.toml"
    adapter = _stub_context(
        CodexAdapter(_memory(), project_root=tmp_path, config_path=config_path)
    )
    result = adapter.install()
    assert result == {"agents_instructions": True, "codex_mcp_config": True}
    assert "visp-memory goal" in (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "VISP_MEMORY_REPO_ID = \"hook-repo\"" in config_path.read_text(encoding="utf-8")
    assert adapter.update_context(task="codex task") is True
    assert "STUBBED CONTEXT" in (tmp_path / "AGENTS.md").read_text(encoding="utf-8")

    removed = adapter.uninstall()
    assert removed == {"agents_instructions": True, "codex_mcp_config": True}
    assert "LLM-MEMORY-CODEX" not in (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "mcp_servers.visp-memory" not in config_path.read_text(encoding="utf-8")

    existing_root = tmp_path / "existing"
    existing_root.mkdir()
    agents = existing_root / "AGENTS.md"
    agents.write_text("# User instructions\n", encoding="utf-8")
    existing = CodexAdapter(
        _memory(), project_root=existing_root, config_path=existing_root / "config.toml"
    )
    assert existing.install()["agents_instructions"] is True
    assert (existing_root / "AGENTS.md.backup").read_text(encoding="utf-8") == (
        "# User instructions\n"
    )


def test_codex_refuses_unmanaged_or_malformed_managed_config(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text("[mcp_servers.visp-memory]\ncommand = \"other\"\n", encoding="utf-8")
    adapter = CodexAdapter(_memory(), project_root=tmp_path, config_path=config_path)
    original = config_path.read_text(encoding="utf-8")
    result = adapter.install()
    assert result == {"agents_instructions": True, "codex_mcp_config": False}
    assert config_path.read_text(encoding="utf-8") == original

    config_path.write_text(
        "# BEGIN LLM-MEMORY CODEX MCP\n# BEGIN LLM-MEMORY CODEX MCP\n",
        encoding="utf-8",
    )
    malformed = config_path.read_text(encoding="utf-8")
    result = adapter.install()
    assert result == {"agents_instructions": True, "codex_mcp_config": False}
    assert config_path.read_text(encoding="utf-8") == malformed


def test_codex_dry_run_is_side_effect_free_for_install_and_remove(tmp_path):
    config_path = tmp_path / "codex.toml"
    adapter = CodexAdapter(
        _memory(), project_root=tmp_path, config_path=config_path, dry_run=True
    )
    assert adapter.install() == {"agents_instructions": True, "codex_mcp_config": True}
    assert adapter.uninstall() == {"agents_instructions": True, "codex_mcp_config": True}
    assert not (tmp_path / "AGENTS.md").exists()
    assert not config_path.exists()


def test_adapter_factory_rejects_unknown_tools():
    with pytest.raises(ValueError, match="Unknown tool"):
        get_adapter("does-not-exist", memory=_memory())


def test_auto_hooks_fail_open_for_unavailable_or_empty_memory(monkeypatch):
    monkeypatch.setattr("visp_memory.hooks.claude_code_auto._make_memory", lambda: None)
    assert handle_session_start({}) is None
    assert handle_pre_tool_use({"tool_input": {"file_path": "a.py"}}) is None

    monkeypatch.setattr(
        "visp_memory.core.injection.build_session_brief", lambda *_args, **_kwargs: "  "
    )
    assert handle_session_start({}, memory=object()) is None


def test_auto_pre_tool_hook_fails_open_when_injection_raises(monkeypatch, tmp_path):
    memory = _memory(data_dir=tmp_path)
    monkeypatch.setattr(
        "visp_memory.core.injection.inject_for_task",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("down")),
    )
    payload = {"session_id": "safe/id", "tool_input": {"file_path": "src/a.py"}}
    assert handle_pre_tool_use(payload, memory=memory) is None


def test_auto_pre_tool_hook_recovers_corrupt_state_and_survives_write_failure(
    tmp_path, monkeypatch
):
    memory = _memory(data_dir=tmp_path)
    state_path = tmp_path / "hook_sessions" / "safe_id.json"
    state_path.parent.mkdir()
    state_path.write_text("not-json", encoding="utf-8")

    abstained = SimpleNamespace(abstained=True, reason="no eligible context")
    monkeypatch.setattr(
        "visp_memory.core.injection.inject_for_task",
        lambda *_args, **_kwargs: abstained,
    )
    payload = {"session_id": "safe/id", "tool_input": {"file_path": "src/a.py"}}
    assert handle_pre_tool_use(payload, memory=memory) is None
    assert json.loads(state_path.read_text(encoding="utf-8")) == ["src/a.py"]

    response_result = SimpleNamespace(
        abstained=False,
        memories=[{"id": "memory-1", "category": "fact", "content": "use timeout"}],
    )
    monkeypatch.setattr(
        "visp_memory.core.injection.inject_for_task",
        lambda *_args, **_kwargs: response_result,
    )
    original_write_text = Path.write_text

    def fail_state_write(path, data, *args, **kwargs):
        if path.name == "write-failure.json":
            raise OSError("state directory unavailable")
        return original_write_text(path, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_state_write)
    response = handle_pre_tool_use(
        {"session_id": "write-failure", "tool_input": {"file_path": "src/b.py"}},
        memory=memory,
    )
    assert response["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert "use timeout" in response["hookSpecificOutput"]["additionalContext"]


def test_auto_hook_installer_rejects_non_object_and_uninstaller_ignores_bad_json(tmp_path):
    settings_path = tmp_path / ".claude" / "settings.json"
    settings_path.parent.mkdir()
    settings_path.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON object"):
        install_auto_inject_hooks(tmp_path)

    settings_path.write_text("not json", encoding="utf-8")
    assert uninstall_auto_inject_hooks(tmp_path) is False
    settings_path.write_text("[]", encoding="utf-8")
    assert uninstall_auto_inject_hooks(tmp_path) is False


def test_session_start_fails_open_when_memory_construction_fails(monkeypatch):
    import visp_memory

    class BrokenMemory:
        def __init__(self):
            raise RuntimeError("storage unavailable")

    monkeypatch.setattr(visp_memory, "Memory", BrokenMemory)
    assert handle_session_start({}) is None
