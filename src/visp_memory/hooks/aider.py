"""
Aider Adapter

Integrates with Aider by creating a .aider file for context.
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
from visp_memory.hooks.generic import GenericAdapter


class AiderAdapter(GenericAdapter):
    """
    Adapter for Aider integration.

    Creates .aider file for context that Aider can read.
    """

    def __init__(self, memory, project_root: Path = None):
        """
        Initialize Aider adapter.

        Args:
            memory: Memory instance
            project_root: Project root directory
        """
        super().__init__(
            memory=memory,
            project_root=project_root,
            context_file=".aider",
            injection_marker=None,  # Aider uses standalone file
            append_mode=False,
        )

    def install(self) -> Dict[str, bool]:
        """
        Install Aider integration.

        Creates .aider file.

        Returns:
            Installation status
        """
        # Seed the file BEFORE delegating: GenericAdapter.install() creates a
        # missing standalone context file, which left this branch unreachable.
        results = {}
        if not self.context_file.exists():
            self._ensure_directory(self.context_file)
            self.context_file.write_text(self._initial_context(), encoding="utf-8")
            results["aider_file_created"] = True

        results.update(super().install())
        return results

    def _initial_context(self) -> str:
        """Render the starter .aider file from the one canonical verb catalogue."""
        return f"""# Aider Context

This project uses visp-memory for persistent context.

Set the direction. {INTENT_LIFECYCLE_HEADLINE}

{render_command_block(INTENT_VERBS)}

{INTENT_NON_AUTHORITATIVE_NOTE}

Record what happened:

{render_command_block(CAPTURE_VERBS)}

Context is automatically updated below.

---
"""

    def update_context(self, files: list[str] = None, task: str = None) -> bool:
        """
        Update memory context in .aider file.

        Args:
            files: Files being worked on
            task: Task description

        Returns:
            True if successful
        """
        # Get memory context
        context = self.get_memory_context(files=files, task=task)

        # Format for Aider
        formatted_context = f"""# Aider Context

## Visp Memory Integration

This project uses visp-memory for persistent context across sessions.

Last updated: {self._get_timestamp()}

---

{context}

---

## Usage

Set the direction. {INTENT_LIFECYCLE_HEADLINE}

{render_command_block(INTENT_VERBS)}

{INTENT_NON_AUTHORITATIVE_NOTE}

After making changes, record them:

{render_command_block(CAPTURE_VERBS)}

Refresh context:
```bash
visp-memory hooks update aider
```
"""

        self.context_file.write_text(formatted_context, encoding="utf-8")
        return True

    def _get_timestamp(self) -> str:
        """Get current timestamp."""

        return utc_now().strftime("%Y-%m-%d %H:%M:%S")
