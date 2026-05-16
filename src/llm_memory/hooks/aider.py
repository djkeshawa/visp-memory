"""
Aider Adapter

Integrates with Aider by creating a .aider file for context.
"""

from pathlib import Path
from typing import Dict

from llm_memory.hooks.generic import GenericAdapter


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
        results = super().install()

        if not self.context_file.exists():
            initial_content = """# Aider Context

This project uses llm-memory for persistent context.

Context is automatically updated below.

---
"""
            self.context_file.write_text(initial_content)
            results["aider_file_created"] = True

        return results

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

## LLM Memory Integration

This project uses llm-memory for persistent context across sessions.

Last updated: {self._get_timestamp()}

---

{context}

---

## Usage

After making changes, record them:
```bash
llm-memory record "description" -c <category>
llm-memory decision "what" "why"
llm-memory bug "issue" --fix "solution"
```

Refresh context:
```bash
llm-memory hooks update aider
```
"""

        self.context_file.write_text(formatted_context)
        return True

    def _get_timestamp(self) -> str:
        """Get current timestamp."""
        from datetime import datetime

        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
