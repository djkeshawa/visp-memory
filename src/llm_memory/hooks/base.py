"""
Base Adapter for LLM Tool Integrations

Defines the interface that all tool adapters must implement.
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, Optional


class LLMToolAdapter(ABC):
    """
    Abstract base class for LLM tool integrations.

    Each adapter knows how to:
    - Install hooks/configuration for a specific tool
    - Update context files with memory
    - Format context appropriately for that tool
    """

    def __init__(self, memory, project_root: Path = None):
        """
        Initialize adapter.

        Args:
            memory: Memory instance
            project_root: Root directory of project (defaults to cwd)
        """
        self.memory = memory
        self.project_root = Path(project_root or Path.cwd())

    @abstractmethod
    def install(self) -> Dict[str, bool]:
        """
        Install hooks/configuration for this tool.

        Returns:
            Dict mapping component name to success status
        """
        pass

    @abstractmethod
    def uninstall(self) -> Dict[str, bool]:
        """
        Remove hooks/configuration for this tool.

        Returns:
            Dict mapping component name to success status
        """
        pass

    @abstractmethod
    def update_context(self, files: list[str] = None, task: str = None) -> bool:
        """
        Update the tool's context with current memory.

        Args:
            files: Optional files being worked on
            task: Optional task description

        Returns:
            True if successful
        """
        pass

    @abstractmethod
    def get_context_file_path(self) -> Path:
        """
        Get the path to the context file used by this tool.

        Returns:
            Path to context file
        """
        pass

    def is_installed(self) -> bool:
        """
        Check if this adapter's hooks/config are installed.

        Returns:
            True if installed
        """
        context_file = self.get_context_file_path()
        return context_file.exists()

    def get_memory_context(
        self, files: list[str] = None, task: str = None, format: str = "markdown"
    ) -> str:
        """
        Get formatted memory context using ProactiveRecall.

        Args:
            files: Files being worked on
            task: Task description
            format: Output format

        Returns:
            Formatted context string
        """
        from llm_memory.recall.proactive import ProactiveRecall

        recall = ProactiveRecall(self.memory)

        if files and task:
            context = recall.find_relevant_for_task(task, files=files)
        elif files:
            if len(files) == 1:
                context = recall.on_file_open(files[0])
            else:
                # Aggregate multiple files
                context = {"warnings": [], "bugs": [], "decisions": [], "knowledge": []}
                for file in files:
                    file_context = recall.on_file_open(file)
                    for key in context:
                        context[key].extend(file_context.get(key, []))
        elif task:
            context = recall.find_relevant_for_task(task)
        else:
            # Full context
            return self.memory.context(format="text")

        return recall.format_injection(context, format=format)

    def _ensure_directory(self, path: Path) -> None:
        """Ensure directory exists for a file path."""
        path.parent.mkdir(parents=True, exist_ok=True)

    def _backup_file(self, path: Path) -> Optional[Path]:
        """Create backup of existing file."""
        if path.exists():
            backup = path.with_suffix(path.suffix + ".backup")
            import shutil

            shutil.copy2(path, backup)
            return backup
        return None


def _replace_between_markers(
    content: str, start: str, end: str, replacement: str
) -> Optional[str]:
    """
    Safely replace the region between two markers.

    Guarantees the file is only rewritten when the markers are well-formed:
    ``start`` must occur exactly once, ``end`` must occur exactly once, and
    ``start`` must appear before ``end``. In any other case (missing, duplicated,
    or reordered markers) this returns ``None`` so callers can refuse to write
    and leave a potentially corrupt/ambiguous file untouched.

    Args:
        content: Current file content.
        start: The start marker string.
        end: The end marker string.
        replacement: Text to place between ``start`` and ``end``. It is inserted
            verbatim between the (preserved) marker strings.

    Returns:
        The rewritten content, or ``None`` if the markers are malformed.
    """
    if content.count(start) != 1 or content.count(end) != 1:
        return None

    start_idx = content.index(start)
    end_idx = content.index(end)
    if start_idx >= end_idx:
        return None

    before = content[:start_idx]
    after = content[end_idx + len(end):]
    return f"{before}{start}{replacement}{end}{after}"
