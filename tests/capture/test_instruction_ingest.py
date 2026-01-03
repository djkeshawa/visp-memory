"""Tests for instruction-file ingestion (static-file zoo -> seed memories)."""

import tempfile
from pathlib import Path

import pytest

from visp_memory import Memory, MemoryConfig
from visp_memory.capture.instructions import (
    INSTRUCTION_CATEGORY,
    discover_instruction_files,
    ingest_instructions,
    split_sections,
)

CLAUDE_MD = """Project uses uv for dependency management and pytest for tests.

## Conventions

- Line length 100, ruff for linting and import order.
- All timestamps must be UTC.

## Warnings

- The Neo4j vector index dimensions must match the embedding provider.

<!-- LLM-MEMORY --> START
This injected block must never be re-ingested.
<!-- LLM-MEMORY --> END
"""

AGENTS_MD = """## Build

Run `make build` before committing anything to this repository please.
"""


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "CLAUDE.md").write_text(CLAUDE_MD, encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text(AGENTS_MD, encoding="utf-8")
    rules = tmp_path / ".cursor" / "rules"
    rules.mkdir(parents=True)
    (rules / "style.mdc").write_text(
        "## Style\n\nPrefer composition over inheritance in service classes.\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def memory():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        config = MemoryConfig()
        config.storage.data_dir = Path(tmpdir)
        config.embedding.provider = "noop"
        yield Memory(config=config)


def test_discovers_known_files(project):
    names = [p.name for p in discover_instruction_files(project)]
    assert "CLAUDE.md" in names
    assert "AGENTS.md" in names
    assert "style.mdc" in names


def test_split_sections_by_heading_and_strips_managed_block():
    sections = split_sections(CLAUDE_MD)
    titles = [title for title, _ in sections]
    assert "(preamble)" in titles
    assert "Conventions" in titles
    assert "Warnings" in titles
    joined = " ".join(body for _, body in sections)
    assert "never be re-ingested" not in joined


def test_ingest_stores_sections_with_provenance(memory, project):
    report = ingest_instructions(memory, root=project)

    assert report.stored > 0
    rows = memory._storage.list_memories(
        layer="semantic", category=INSTRUCTION_CATEGORY, limit=100
    )
    assert len(rows) == report.stored
    sample = rows[0]
    assert sample["metadata"]["source_file"]
    assert sample["metadata"]["content_hash"]
    assert "imported_instruction" in sample["tags"]


def test_reingest_is_idempotent(memory, project):
    first = ingest_instructions(memory, root=project)
    second = ingest_instructions(memory, root=project)

    assert second.stored == 0
    assert second.skipped_unchanged >= first.stored
    rows = memory._storage.list_memories(
        layer="semantic", category=INSTRUCTION_CATEGORY, limit=100
    )
    assert len(rows) == first.stored


def test_edited_section_adds_only_the_change(memory, project):
    ingest_instructions(memory, root=project)
    claude = project / "CLAUDE.md"
    claude.write_text(
        CLAUDE_MD + "\n## New Rule\n\nNever commit directly to the develop branch.\n",
        encoding="utf-8",
    )

    delta = ingest_instructions(memory, root=project)
    assert delta.stored == 1


def test_ingested_sections_are_recallable(memory, project):
    ingest_instructions(memory, root=project)
    results = memory.recall("timestamps UTC convention", min_score=0.0)
    assert any("UTC" in row["content"] for row in results)


def test_dry_run_stores_nothing(memory, project):
    report = ingest_instructions(memory, root=project, dry_run=True)
    assert report.stored > 0
    rows = memory._storage.list_memories(
        layer="semantic", category=INSTRUCTION_CATEGORY, limit=100
    )
    assert rows == []


def test_empty_project_reports_no_files(memory, tmp_path):
    report = ingest_instructions(memory, root=tmp_path)
    assert report.files == []
    assert report.stored == 0
