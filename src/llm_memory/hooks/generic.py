"""
Generic Adapter for File-Based Context Injection

Works with any LLM tool that reads a context file.
Supports two modes:
1. Standalone file (e.g., .llm-context.md)
2. Injection into existing file with markers
"""

from pathlib import Path
from typing import Dict


from llm_memory.hooks.base import LLMToolAdapter


class GenericAdapter(LLMToolAdapter):
    """
    Generic adapter that writes context to a file.

    Can either:
    - Write to standalone context file
    - Inject into existing file between markers

    Usage:
        # Standalone file
        adapter = GenericAdapter(memory, context_file=".llm-context.md")
        adapter.install()
        adapter.update_context()

        # Inject into existing file
        adapter = GenericAdapter(
            memory,
            context_file="CLAUDE.md",
            injection_marker="<!-- LLM-MEMORY -->",
            append_mode=True
        )
    """

    def __init__(
        self,
        memory,
        project_root: Path = None,
        context_file: str = ".llm-context.md",
        injection_marker: str = None,
        append_mode: bool = False
    ):
        """
        Initialize generic adapter.

        Args:
            memory: Memory instance
            project_root: Project root directory
            context_file: Name of context file
            injection_marker: Marker for injection (e.g., "<!-- MEMORY -->")
            append_mode: If True, append to file; if False, replace section
        """
        super().__init__(memory, project_root)
        self.context_file = self.project_root / context_file
        self.injection_marker = injection_marker
        self.append_mode = append_mode

    def install(self) -> Dict[str, bool]:
        """
        Create the context file or prepare for injection.

        Returns:
            Installation status
        """
        results = {}

        if self.injection_marker:
            # Injection mode - check target file exists
            if not self.context_file.exists():
                # Create it with markers
                self._ensure_directory(self.context_file)
                self.context_file.write_text(
                    f"{self.injection_marker} START\n\n"
                    f"{self.injection_marker} END\n"
                )
                results["injection_markers"] = True
            else:
                # Check if markers exist
                content = self.context_file.read_text()
                if self.injection_marker in content:
                    results["injection_markers"] = True
                else:
                    # Add markers
                    if self.append_mode:
                        with self.context_file.open("a") as f:
                            f.write(f"\n\n{self.injection_marker} START\n\n")
                            f.write(f"{self.injection_marker} END\n")
                    else:
                        # Insert at top
                        existing = self.context_file.read_text()
                        new_content = (
                            f"{self.injection_marker} START\n\n"
                            f"{self.injection_marker} END\n\n"
                            f"{existing}"
                        )
                        self.context_file.write_text(new_content)
                    results["injection_markers"] = True
        else:
            # Standalone file mode
            if not self.context_file.exists():
                self._ensure_directory(self.context_file)
                self.context_file.write_text("# LLM Memory Context\n\n")
                results["context_file"] = True
            else:
                results["context_file"] = True  # Already exists

        return results

    def uninstall(self) -> Dict[str, bool]:
        """
        Remove context file or injection markers.

        Returns:
            Uninstallation status
        """
        results = {}

        if self.injection_marker:
            # Remove injected section
            if self.context_file.exists():
                content = self.context_file.read_text()

                # Find and remove marked section
                start_marker = f"{self.injection_marker} START"
                end_marker = f"{self.injection_marker} END"

                if start_marker in content and end_marker in content:
                    # Split and remove section
                    before = content.split(start_marker)[0]
                    after = content.split(end_marker)[1] if end_marker in content else ""

                    new_content = before + after
                    self.context_file.write_text(new_content.strip() + "\n")
                    results["injection_removed"] = True
                else:
                    results["injection_removed"] = False  # Not found
            else:
                results["injection_removed"] = True  # File doesn't exist
        else:
            # Remove standalone file
            if self.context_file.exists():
                self.context_file.unlink()
                results["context_file"] = True
            else:
                results["context_file"] = True  # Already removed

        return results

    def update_context(
        self,
        files: list[str] = None,
        task: str = None
    ) -> bool:
        """
        Update context in the file.

        Args:
            files: Files being worked on
            task: Task description

        Returns:
            True if successful
        """
        # Get memory context
        context = self.get_memory_context(files=files, task=task)

        # Add header
        full_context = (
            "# LLM Memory Context\n\n"
            "*Auto-generated from llm-memory. Do not edit manually.*\n\n"
            f"{context}\n"
        )

        if self.injection_marker:
            # Injection mode
            if not self.context_file.exists():
                self.install()

            content = self.context_file.read_text()

            start_marker = f"{self.injection_marker} START"
            end_marker = f"{self.injection_marker} END"

            # Replace content between markers
            if start_marker in content and end_marker in content:
                before = content.split(start_marker)[0]
                after = content.split(end_marker)[1]

                new_content = (
                    f"{before}{start_marker}\n\n"
                    f"{full_context}\n"
                    f"{end_marker}{after}"
                )

                self.context_file.write_text(new_content)
                return True
            else:
                # Markers not found - can't inject
                return False
        else:
            # Standalone mode - replace entire file
            self.context_file.write_text(full_context)
            return True

    def get_context_file_path(self) -> Path:
        """Get path to context file."""
        return self.context_file
