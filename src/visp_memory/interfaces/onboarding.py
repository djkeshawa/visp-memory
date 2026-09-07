"""An isolated first-session walkthrough using the real capture and recall paths."""

from pathlib import Path
from tempfile import TemporaryDirectory

from rich.console import Console

from visp_memory import Memory, MemoryConfig
from visp_memory.config import EmbeddingConfig, StorageConfig
from visp_memory.core.trust import WriteChannel


def run_demo(console: Console) -> None:
    """Verify a sample decision in a disposable local store, never project state."""
    console.print("Local demo: temporary SQLite store, no embeddings or model calls.")
    with TemporaryDirectory(prefix="visp-memory-demo-") as directory:
        config = MemoryConfig(
            project_name="demo",
            repo_id="demo",
            storage=StorageConfig(data_dir=Path(directory), backend="sqlite", mode="local"),
            embedding=EmbeddingConfig(provider="noop"),
        )
        with Memory(config=config) as memory:
            decision = "Screen wrap on all four edges"
            reason = "Bouncing was rejected in the spec"
            console.print(f"1. Capture: {decision} — {reason}")
            memory_id = memory.decision(decision, reason, _write_channel=WriteChannel.CLI)
            results = memory.recall("screen wrap")
            if not any(row["id"] == memory_id for row in results):
                raise ValueError("Demo capture/recall round trip failed; no success recorded.")
            console.print(f"2. Recall: {decision} (ID: {memory_id})")
            console.print("[green]Capture and recall verified.[/green]")
    console.print("Sample store removed. This verifies local storage, not agent integration.")
    console.print("In your project: visp-memory init")
    console.print("Connect your assistant: visp-memory hooks install codex")
    console.print("Or choose claude-code, cursor, aider, or generic. Check: visp-memory doctor")
    console.print('Record a real decision, then: visp-memory brief "your concrete task"')
