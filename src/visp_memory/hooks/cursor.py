"""
Cursor Adapter

Integrates with Cursor IDE by injecting context into .cursorrules file.
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


class CursorAdapter(GenericAdapter):
    """
    Adapter for Cursor IDE integration.

    Uses .cursorrules for context injection.
    """

    def __init__(self, memory, project_root: Path = None):
        """
        Initialize Cursor adapter.

        Args:
            memory: Memory instance
            project_root: Project root directory
        """
        super().__init__(
            memory=memory,
            project_root=project_root,
            context_file=".cursorrules",
            injection_marker="# LLM-MEMORY",
            append_mode=True,
        )

    def install(self) -> Dict[str, bool]:
        """
        Install Cursor integration.

        Creates .cursorrules if it doesn't exist, or adds markers if it does.

        Returns:
            Installation status
        """
        # Seed the file BEFORE delegating: GenericAdapter.install() creates a
        # missing context file containing only the markers, which left this
        # branch unreachable and every fresh .cursorrules verb-free.
        results = {}
        if not self.context_file.exists():
            self._ensure_directory(self.context_file)
            self.context_file.write_text(self._initial_rules(), encoding="utf-8")
            results["cursorrules_created"] = True

        results.update(super().install())
        return results

    def _initial_rules(self) -> str:
        """Render the starter .cursorrules from the one canonical verb catalogue."""
        return f"""# Cursor Rules

## Visp Memory Integration

This project uses visp-memory for persistent context.

Set the direction. {INTENT_LIFECYCLE_HEADLINE}

{render_command_block(INTENT_VERBS)}

{INTENT_NON_AUTHORITATIVE_NOTE}

Record what happened:

{render_command_block(CAPTURE_VERBS)}

Memory context is automatically injected below:

# LLM-MEMORY START

# LLM-MEMORY END
"""

    def update_context(self, files: list[str] = None, task: str = None) -> bool:
        """
        Update memory context in .cursorrules.

        Args:
            files: Files being worked on
            task: Task description

        Returns:
            True if successful
        """
        # Get memory context
        context = self.get_memory_context(files=files, task=task)

        # Format for .cursorrules (plain text, no markdown decorations)
        formatted_context = f"""## Memory Context

Auto-updated by visp-memory
Last updated: {self._get_timestamp()}

{context}
"""

        if not self.context_file.exists():
            self.install()

        content = self.context_file.read_text(encoding="utf-8")

        start_marker = "# LLM-MEMORY START"
        end_marker = "# LLM-MEMORY END"

        # Only rewrite when the markers are well-formed (present once each,
        # start before end). Malformed/duplicated/reordered markers => refuse.
        new_content = _replace_between_markers(
            content, start_marker, end_marker, f"\n\n{formatted_context}\n"
        )
        if new_content is None:
            return False

        self.context_file.write_text(new_content, encoding="utf-8")
        return True

    def _get_timestamp(self) -> str:
        """Get current timestamp."""

        return utc_now().strftime("%Y-%m-%d %H:%M:%S")
