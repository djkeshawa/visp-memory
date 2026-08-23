"""
Claude Code Adapter

Integrates with Claude Code by:
1. Injecting context into CLAUDE.md
2. (Future) Setting up Claude Code hooks for auto-update
"""

from pathlib import Path
from typing import Dict

from visp_memory.core.clock import utc_now
from visp_memory.core.verbs import (
    CAPTURE_VERBS,
    INTENT_LIFECYCLE_HEADLINE,
    INTENT_NON_AUTHORITATIVE_NOTE,
    INTENT_VERBS,
    render_command_block,
)
from visp_memory.hooks.base import _replace_between_markers
from visp_memory.hooks.generic import GenericAdapter


class ClaudeCodeAdapter(GenericAdapter):
    """
    Adapter for Claude Code integration.

    Uses CLAUDE.md for context injection with special markers.

    The marker format is compatible with Claude Code's documentation
    convention.
    """

    def __init__(self, memory, project_root: Path = None):
        """
        Initialize Claude Code adapter.

        Args:
            memory: Memory instance
            project_root: Project root directory
        """
        super().__init__(
            memory=memory,
            project_root=project_root,
            context_file="CLAUDE.md",
            injection_marker="<!-- LLM-MEMORY -->",
            append_mode=True,  # Append to existing CLAUDE.md
        )

    def install(self) -> Dict[str, bool]:
        """
        Install Claude Code integration.

        Creates CLAUDE.md if it doesn't exist, or adds markers if it does.

        Returns:
            Installation status
        """
        # Seed the file BEFORE delegating. GenericAdapter.install() creates a
        # missing context file containing nothing but the markers, so the
        # "if not exists" branch that used to follow it could never run: every
        # fresh install produced a CLAUDE.md that named no memory verbs at all.
        results = {}
        if not self.context_file.exists():
            self._ensure_directory(self.context_file)
            self.context_file.write_text(self._initial_instructions(), encoding="utf-8")
            results["claude_md_created"] = True

        results.update(super().install())
        return results

    def _initial_instructions(self) -> str:
        """Render the starter CLAUDE.md from the one canonical verb catalogue."""
        return f"""# CLAUDE.md

This file provides guidance to Claude Code when working with code in this repository.

## Visp Memory Integration

This project uses visp-memory for persistent context across sessions.

### Usage

**At the start of each session**, the memory context below is automatically updated.

**Before and during the work, set the direction.** {INTENT_LIFECYCLE_HEADLINE}

{render_command_block(INTENT_VERBS)}

{INTENT_NON_AUTHORITATIVE_NOTE}

Use `visp-memory intent list` to see what is already active, and
`visp-memory intent update|complete|close <intent-id>` to revise or record an
outcome against one.

**After making changes**, record what happened:

{render_command_block(CAPTURE_VERBS)}

---

<!-- LLM-MEMORY --> START

<!-- LLM-MEMORY --> END
"""

    def update_context(self, files: list[str] = None, task: str = None) -> bool:
        """
        Update memory context in CLAUDE.md.

        Args:
            files: Files being worked on
            task: Task description

        Returns:
            True if successful
        """
        # Get memory context with special formatting for Claude
        context = self.get_memory_context(files=files, task=task)

        # Format for CLAUDE.md
        formatted_context = f"""## Memory Context

*Auto-updated by visp-memory. Last updated: {self._get_timestamp()}*

{context}

---
"""

        if not self.context_file.exists():
            self.install()

        # Use parent's injection logic
        content = self.context_file.read_text(encoding="utf-8")

        start_marker = "<!-- LLM-MEMORY --> START"
        end_marker = "<!-- LLM-MEMORY --> END"

        # Only rewrite when the markers are well-formed (present once each,
        # start before end). Malformed/duplicated/reordered markers => refuse.
        new_content = _replace_between_markers(
            content, start_marker, end_marker, f"\n\n{formatted_context}"
        )
        if new_content is None:
            return False

        self.context_file.write_text(new_content, encoding="utf-8")
        return True

    def _get_timestamp(self) -> str:
        """Get current timestamp for context."""

        return utc_now().strftime("%Y-%m-%d %H:%M:%S")
