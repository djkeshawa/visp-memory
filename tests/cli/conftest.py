"""Shared CLI test fixtures (extracted from test_cli_integration for reuse)."""

import os
import tempfile
from pathlib import Path

import pytest


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

    yield temp_dir

    cli_module._memory = None
    os.chdir(old_cwd)
    os.environ.pop("VISP_MEMORY_STORAGE_BACKEND", None)
    os.environ.pop("VISP_MEMORY_EMBEDDING_PROVIDER", None)
