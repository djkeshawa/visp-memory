"""
Claude Code Adapter

Integrates with Claude Code by:
1. Injecting context into CLAUDE.md
2. (Future) Setting up Claude Code hooks for auto-update
"""

from pathlib import Path
from typing import Dict

from llm_memory.hooks.generic import GenericAdapter


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
            append_mode=True  # Append to existing CLAUDE.md
        )

    def install(self) -> Dict[str, bool]:
        """
        Install Claude Code integration.

        Creates CLAUDE.md if it doesn't exist, or adds markers if it does.

        Returns:
            Installation status
        """
        results = super().install()

        # Add note to CLAUDE.md about memory system
        if not self.context_file.exists():
            # Create initial CLAUDE.md with instructions
            initial_content = """# CLAUDE.md

This file provides guidance to Claude Code when working with code in this repository.

## LLM Memory Integration

This project uses llm-memory for persistent context across sessions.

### Usage

**At the start of each session**, the memory context below is automatically updated.

**After making changes**, record them:
```bash
llm-memory record "description" -c <category>
llm-memory decision "what" "why" --alt "alternative"
llm-memory bug "issue" --fix "solution"
```

**When learning something important**:
```bash
llm-memory learn "knowledge" -c <category>
llm-memory warn "area" "warning"
```

---

<!-- LLM-MEMORY --> START

<!-- LLM-MEMORY --> END
"""
            self.context_file.write_text(initial_content)
            results["claude_md_created"] = True

        return results

    def update_context(
        self,
        files: list[str] = None,
        task: str = None
    ) -> bool:
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

*Auto-updated by llm-memory. Last updated: {self._get_timestamp()}*

{context}

---
"""

        if not self.context_file.exists():
            self.install()

        # Use parent's injection logic
        content = self.context_file.read_text()

        start_marker = "<!-- LLM-MEMORY --> START"
        end_marker = "<!-- LLM-MEMORY --> END"

        if start_marker in content and end_marker in content:
            before = content.split(start_marker)[0]
            after = content.split(end_marker)[1]

            new_content = (
                f"{before}{start_marker}\n\n"
                f"{formatted_context}"
                f"{end_marker}{after}"
            )

            self.context_file.write_text(new_content)
            return True
        else:
            return False

    def _get_timestamp(self) -> str:
        """Get current timestamp for context."""
        from datetime import datetime
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
