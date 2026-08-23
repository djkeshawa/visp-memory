"""Shared CLI test fixtures (extracted from test_cli_integration for reuse)."""

import os
import tempfile
from pathlib import Path

import pytest
from rich.console import Console

# Rich sizes its tables from the console it is printing to, so an assertion on
# CLI output is an assertion about the runner's terminal unless the width is
# pinned. Unpinned, Linux CI falls back to 80 while a Windows runner reports its
# own console and then subtracts one for `legacy_windows` -- and at 79 the recall
# table wraps "Screen wrap" across two lines, which is how a passing test became
# a Windows-only failure. 80 would restore it, but only by one column; 120 puts
# real distance between the wrap point and the assertions.
#
# Two things this must stay as. Set it on the Console, not through COLUMNS: Rich
# subtracts `legacy_windows` from a COLUMNS-derived width but returns an explicit
# one verbatim. And set width alone -- `Console(width=..., height=...)` takes a
# different branch of `Console.size` that subtracts `legacy_windows` again, which
# is the off-by-one this constant exists to remove.
CONSOLE_WIDTH = 120


@pytest.fixture
def temp_dir():
    """Create a temporary directory for each test."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def cli_env(temp_dir):
    """Isolated CLI environment: sqlite backend, noop embeddings, fresh Memory."""
    old_cwd = os.getcwd()
    os.chdir(temp_dir)

    os.environ["VISP_MEMORY_STORAGE_BACKEND"] = "sqlite"
    os.environ["VISP_MEMORY_EMBEDDING_PROVIDER"] = "noop"

    import visp_memory.interfaces.cli as cli_module

    cli_module._memory = None
    # Swap the Console rather than assign to `console.width`: the width getter
    # resolves to a concrete number, so restoring through it would leave the
    # process-wide console pinned for every later test instead of unpinned.
    original_console = cli_module.console
    cli_module.console = Console(width=CONSOLE_WIDTH)

    yield temp_dir

    cli_module.console = original_console
    cli_module._memory = None
    os.chdir(old_cwd)
    os.environ.pop("VISP_MEMORY_STORAGE_BACKEND", None)
    os.environ.pop("VISP_MEMORY_EMBEDDING_PROVIDER", None)
