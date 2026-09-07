"""Automatic memory injection for Claude Code via real executable hooks.

The static CLAUDE.md integration only helps when the agent reads the file, and
MCP tools only help when the agent decides to call them. Real hooks close that
gap: Claude Code itself invokes visp-memory at the right moments and the returned
context is injected automatically — no tool call required.

Two hooks are installed into ``.claude/settings.json``:

- **SessionStart** → ``visp-memory hook session-start``: injects the compact
  project memory context (goals, constraints, warnings, conventions) when a
  session begins.
- **PreToolUse** (matcher ``Read|Edit|Write``) → ``visp-memory hook pre-tool-use``:
  before the agent reads or edits a file, injects that file's warnings, past
  bugs, and decisions — the recall-before-work loop, automated.

Design constraints:

- **Fail-open.** A memory problem must never break the user's coding session:
  handlers catch everything and return no output on failure; the CLI always
  exits 0.
- **No permission interference.** Hook output carries only ``additionalContext``;
  it never sets ``permissionDecision``.
- **No repeat spam.** Injected file paths are remembered per Claude session (a
  small state file under the data dir), so a file's context is injected once
  per session, not on every Read of the same file.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

HOOK_COMMAND_PREFIX = "visp-memory hook"
SESSION_START_COMMAND = "visp-memory hook session-start"
PRE_TOOL_USE_COMMAND = "visp-memory hook pre-tool-use"
PRE_TOOL_USE_MATCHER = "Read|Edit|Write|MultiEdit|NotebookEdit"

# Budgets, not truncation limits. The old values (4,000 / 1,500 characters of whatever
# the store returned) were roughly 1,400 tokens of unranked context on every session and
# every file touch. SWE-ContextBench measured that shape of injection as costing more
# than no memory at all; see visp_memory.core.injection for the evidence and policy.
SESSION_CONTEXT_MAX_CHARS = 900
FILE_CONTEXT_MAX_CHARS = 700
_SESSION_ID_RE = re.compile(r"[^A-Za-z0-9_-]")
_MAX_TRACKED_FILES = 200


# =============================================================================
# Runtime handlers (invoked by Claude Code via the `visp-memory hook` CLI)
# =============================================================================


def handle_session_start(payload: dict[str, Any], memory=None) -> Optional[dict[str, Any]]:
    """Return SessionStart hook output carrying a compact project brief.

    Deliberately small: see :func:`visp_memory.core.injection.build_session_brief` for
    why recent history is excluded and only explicit intent plus warnings are injected.
    """
    try:
        memory = memory or _make_memory()
        if memory is None:
            return None

        from visp_memory.core.injection import build_session_brief

        brief = build_session_brief(memory, max_chars=SESSION_CONTEXT_MAX_CHARS)
        if not brief.strip():
            return None
        return {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": (
                    "Project memory (from visp-memory; recall more with the memory_* "
                    "MCP tools):\n\n" + brief
                ),
            }
        }
    except Exception as exc:  # fail-open: never break the user's session
        logger.debug("session-start hook failed open: %s", exc)
        return None


def handle_pre_tool_use(payload: dict[str, Any], memory=None) -> Optional[dict[str, Any]]:
    """Return PreToolUse hook output with memory context for the touched file."""
    try:
        file_path = _extract_file_path(payload)
        if not file_path:
            return None

        session_id = str(payload.get("session_id") or "default")
        memory = memory or _make_memory()
        if memory is None:
            return None

        state_path = _session_state_path(memory, session_id)
        injected = _load_injected(state_path)
        normalized = str(file_path).replace("\\", "/")
        if normalized in injected:
            return None

        from visp_memory.core.injection import (
            InjectionPolicy,
            format_injection,
            inject_for_task,
        )

        result = inject_for_task(
            memory,
            task=f"working on {normalized}",
            files=[normalized],
            policy=InjectionPolicy(max_chars=FILE_CONTEXT_MAX_CHARS),
        )
        # Always mark the file as seen so empty lookups are not repeated either.
        _save_injected(state_path, injected + [normalized])
        if result.abstained:
            logger.debug("no injection for %s: %s", normalized, result.reason)
            return None

        formatted = format_injection(result, header=f"Memory for {normalized}")
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": f"{formatted}\n\n_(from visp-memory)_",
            }
        }
    except Exception as exc:  # fail-open
        logger.debug("pre-tool-use hook failed open: %s", exc)
        return None


def _make_memory():
    try:
        from visp_memory import Memory

        return Memory()
    except Exception as exc:
        logger.debug("hook could not initialize memory: %s", exc)
        return None


def _extract_file_path(payload: dict[str, Any]) -> Optional[str]:
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return None
    for key in ("file_path", "path", "notebook_path"):
        value = tool_input.get(key)
        if value:
            return str(value)
    return None


def _session_state_path(memory, session_id: str) -> Path:
    safe = _SESSION_ID_RE.sub("_", session_id)[:64] or "default"
    state_dir = Path(memory.config.storage.data_dir) / "hook_sessions"
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir / f"{safe}.json"


def _load_injected(state_path: Path) -> list[str]:
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [str(item) for item in data]
    except (OSError, ValueError):
        pass
    return []


def _save_injected(state_path: Path, injected: list[str]) -> None:
    try:
        state_path.write_text(
            json.dumps(injected[-_MAX_TRACKED_FILES:]), encoding="utf-8"
        )
    except OSError:
        pass


# =============================================================================
# Installer (writes real hooks into .claude/settings.json)
# =============================================================================


def _hook_entries() -> dict[str, list[dict[str, Any]]]:
    return {
        "SessionStart": [
            {
                "hooks": [
                    {"type": "command", "command": SESSION_START_COMMAND, "timeout": 30}
                ]
            }
        ],
        "PreToolUse": [
            {
                "matcher": PRE_TOOL_USE_MATCHER,
                "hooks": [
                    {"type": "command", "command": PRE_TOOL_USE_COMMAND, "timeout": 15}
                ],
            }
        ],
    }


def _is_ours(entry: dict[str, Any]) -> bool:
    for hook in entry.get("hooks", []):
        if str(hook.get("command", "")).startswith(HOOK_COMMAND_PREFIX):
            return True
    return False


def install_auto_inject_hooks(project_root: Path | str = ".", *, dry_run: bool = False) -> Path:
    """Merge visp-memory's SessionStart/PreToolUse hooks into .claude/settings.json.

    Idempotent: existing visp-memory entries are replaced, everything else in the
    settings file is preserved. Raises ValueError if the file exists but is not
    valid JSON (never clobber a file we cannot parse).
    """
    settings_path = Path(project_root) / ".claude" / "settings.json"
    settings: dict[str, Any] = {}
    if settings_path.exists():
        text = settings_path.read_text(encoding="utf-8")
        if text.strip():
            try:
                settings = json.loads(text)
            except ValueError as exc:
                raise ValueError(
                    f"{settings_path} is not valid JSON; refusing to modify it"
                ) from exc
            if not isinstance(settings, dict):
                raise ValueError(f"{settings_path} must contain a JSON object")

    hooks = settings.setdefault("hooks", {})
    for event, entries in _hook_entries().items():
        existing = [entry for entry in hooks.get(event, []) if not _is_ours(entry)]
        hooks[event] = existing + entries

    if not dry_run:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    return settings_path


def uninstall_auto_inject_hooks(project_root: Path | str = ".") -> bool:
    """Remove visp-memory's hook entries from .claude/settings.json.

    Returns True if anything was removed. Other settings are preserved.
    """
    settings_path = Path(project_root) / ".claude" / "settings.json"
    if not settings_path.exists():
        return False
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except ValueError:
        return False
    if not isinstance(settings, dict):
        return False

    hooks = settings.get("hooks") or {}
    removed = False
    for event in list(hooks):
        kept = [entry for entry in hooks[event] if not _is_ours(entry)]
        if len(kept) != len(hooks[event]):
            removed = True
        if kept:
            hooks[event] = kept
        else:
            hooks.pop(event)
    if not hooks and "hooks" in settings:
        settings.pop("hooks")

    if removed:
        settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    return removed
