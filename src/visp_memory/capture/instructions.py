"""Ingest existing assistant instruction files as seed memories.

Developers already maintain hand-written context for their tools — CLAUDE.md,
AGENTS.md, .cursorrules, .cursor/rules/*.mdc, copilot-instructions.md. Today each
tool re-reads its own file wholesale every session (a flat token tax with no
relevance ranking). Ingesting them as semantic memories turns that static zoo
into relevance-ranked, decaying, recallable knowledge — instant day-one value
for a new visp-memory install.

Idempotent by content hash: re-running ingestion after edits only stores new or
changed sections; unchanged sections are skipped.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from visp_memory.core.clock import utc_now_iso

INSTRUCTION_CATEGORY = "instruction"
INSTRUCTION_TAG = "imported_instruction"
# Regions visp-memory itself injects into instruction files must not be
# re-ingested, or memory would echo back into itself on every run.
_MANAGED_BLOCK_RE = re.compile(
    r"<!--\s*LLM-MEMORY\s*-->\s*START.*?<!--\s*LLM-MEMORY\s*-->\s*END",
    re.DOTALL,
)
_HEADING_RE = re.compile(r"^#{1,6}\s+(.+)$", re.MULTILINE)

# Well-known instruction files, relative to the project root. Globs allowed.
INSTRUCTION_FILE_PATTERNS = (
    "CLAUDE.md",
    "AGENTS.md",
    ".cursorrules",
    ".cursor/rules/*.mdc",
    ".github/copilot-instructions.md",
    ".github/instructions/*.instructions.md",
)

MIN_SECTION_CHARS = 40  # skip empty/heading-only fragments
MAX_SECTION_CHARS = 4000  # keep individual memories recall-sized


@dataclass
class IngestReport:
    """Summary of one ingestion run."""

    stored: int = 0
    skipped_unchanged: int = 0
    skipped_trivial: int = 0
    files: list[str] = field(default_factory=list)
    memory_ids: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "stored": self.stored,
            "skipped_unchanged": self.skipped_unchanged,
            "skipped_trivial": self.skipped_trivial,
            "files": self.files,
            "memory_ids": self.memory_ids,
        }


def discover_instruction_files(root: Path) -> list[Path]:
    """Find well-known instruction files under a project root, deterministically."""
    root = Path(root)
    found: list[Path] = []
    for pattern in INSTRUCTION_FILE_PATTERNS:
        found.extend(path for path in sorted(root.glob(pattern)) if path.is_file())
    return found


def split_sections(text: str) -> list[tuple[str, str]]:
    """Split markdown into (section_title, section_text) chunks by heading.

    Content before the first heading becomes a "(preamble)" section. Managed
    visp-memory blocks are removed first.
    """
    cleaned = _MANAGED_BLOCK_RE.sub("", text)
    matches = list(_HEADING_RE.finditer(cleaned))
    sections: list[tuple[str, str]] = []

    preamble = cleaned[: matches[0].start()] if matches else cleaned
    if preamble.strip():
        sections.append(("(preamble)", preamble.strip()))

    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(cleaned)
        body = cleaned[match.start() : end].strip()
        if body:
            sections.append((match.group(1).strip(), body))
    return sections


def content_hash(text: str) -> str:
    normalized = " ".join(text.split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _existing_hashes(storage, repo_id: str | None) -> set[str]:
    try:
        rows: Iterable[dict[str, Any]] = storage.list_memories(
            layer="semantic",
            category=INSTRUCTION_CATEGORY,
            repo_id=repo_id,
            status="all",
            limit=10000,
        )
    except Exception:
        return set()
    hashes = set()
    for row in rows:
        stored_hash = (row.get("metadata") or {}).get("content_hash")
        if stored_hash:
            hashes.add(stored_hash)
    return hashes


def ingest_instructions(
    memory,
    root: Path | str = ".",
    repo_id: str = None,
    importance: float = 0.65,
    dry_run: bool = False,
) -> IngestReport:
    """Import instruction-file sections as semantic memories.

    Each markdown section becomes one memory (category="instruction") carrying
    provenance (source file, section title, content hash). Sections whose hash
    already exists are skipped, so repeated runs are idempotent and edits only
    add the changed sections.
    """
    root = Path(root)
    repo_id = repo_id or memory.config.repo_id
    report = IngestReport()
    seen_hashes = _existing_hashes(memory._storage, repo_id)

    for path in discover_instruction_files(root):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        relative = path.relative_to(root).as_posix()
        report.files.append(relative)

        for title, body in split_sections(text):
            if len(body) < MIN_SECTION_CHARS:
                report.skipped_trivial += 1
                continue
            body = body[:MAX_SECTION_CHARS]
            section_hash = content_hash(body)
            if section_hash in seen_hashes:
                report.skipped_unchanged += 1
                continue
            seen_hashes.add(section_hash)
            if dry_run:
                report.stored += 1
                continue
            memory_id = memory._storage.store_memory(
                content=body,
                layer="semantic",
                category=INSTRUCTION_CATEGORY,
                importance=importance,
                repo_id=repo_id,
                tags=[INSTRUCTION_TAG],
                metadata={
                    "source_file": relative,
                    "section": title,
                    "content_hash": section_hash,
                    "ingested_at": utc_now_iso(),
                },
                source="instruction_ingest",
            )
            report.stored += 1
            report.memory_ids.append(memory_id)

    return report
