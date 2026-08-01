"""First-run project bootstrap: turn existing history into useful memory.

An empty memory store is worth nothing, and nobody will hand-write memories for weeks
to find out whether the idea works. Every repository already contains the raw material,
though: commit history records what was tried, what broke, what was reverted, and which
files keep needing repair.

This module mines that, but **selectively**. A bulk import of every commit would be the
same mistake the injection policy exists to prevent -- most commits are ``chore: bump
version`` and ``style: satisfy ruff``, and a store full of those makes recall worse, not
better. The research this project is built on is explicit that unfiltered context is
worth less than no context, so the bootstrap keeps only commits carrying signal:

- **Reverts** become warnings. A revert is the strongest available evidence that an
  approach was tried and failed, and it is exactly what an assistant needs to not
  suggest it again.
- **Repeatedly fixed files** become warnings. A file with several independent bug fixes
  is empirically fragile, which no amount of reading the current code reveals.
- **Commits that explain themselves** become decisions. A message containing "because",
  "so that", or "instead of" carries the rationale that code alone cannot.
- **Fixes and features** become episodes at their natural importance.
- Everything else is skipped and counted, so the report is honest about what was dropped.

Instruction files (``README``, ``CLAUDE.md``, ``AGENTS.md``, ADRs) are ingested through
:mod:`visp_memory.capture.instructions`, which already handles sectioning and dedup.
"""

from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from visp_memory.core.trust import WriteChannel

logger = logging.getLogger(__name__)

# Conventional-commit prefixes with no durable recall value. A memory store full of
# these dilutes every query that follows.
LOW_SIGNAL_PREFIXES = frozenset({"chore", "style", "ci", "build", "deps", "release"})

# Messages that are pure mechanics regardless of prefix.
_LOW_SIGNAL_PATTERNS = (
    re.compile(r"^\s*(bump|update|upgrade|pin)\b.*\bversion\b", re.I),
    re.compile(r"^\s*v?\d+\.\d+\.\d+\s*$"),
    re.compile(r"^\s*merge\b", re.I),
    re.compile(r"^\s*(wip|tmp|temp|fixup|squash)\b", re.I),
    re.compile(r"^\s*(initial commit|first commit)\s*$", re.I),
)

_REVERT_RE = re.compile(r"^\s*(revert|rollback)\b", re.I)

# Rationale markers: the words people use when explaining *why*, which is the part that
# never survives in the code itself.
_RATIONALE_RE = re.compile(
    r"\b(because|so that|instead of|rather than|in order to|the reason|"
    r"we decided|decided to|to avoid|to prevent|turns out)\b",
    re.I,
)

_FIX_RE = re.compile(r"^\s*(fix|bug|hotfix|patch)\b", re.I)

# A file needs at least this many independent fix commits before "historically fragile"
# is a claim rather than a coincidence.
FRAGILE_FILE_MIN_FIXES = 3

# Files that change constantly for uninteresting reasons.
_IGNORED_PATH_RE = re.compile(
    r"(^|/)(package-lock\.json|uv\.lock|poetry\.lock|yarn\.lock|"
    r"CHANGELOG\.md|\.github/)", re.I
)

# Fragility is a claim about *code*. Documentation accumulates "fix" commits for typos
# and stale instructions, which says nothing about whether editing it is risky; mining
# this repository flagged README.md as historically fragile, which is worse than useless.
_NON_CODE_PATH_RE = re.compile(
    r"(\.(md|rst|txt|json|ya?ml|toml|ini|cfg|lock|svg|png|jpe?g|gif|ico)$"
    r"|(^|/)(docs?|examples?)/)",
    re.I,
)

DEFAULT_COMMIT_LIMIT = 400


@dataclass
class BootstrapReport:
    """What the first run found, including what it deliberately ignored."""

    commits_scanned: int = 0
    skipped_low_signal: int = 0
    decisions: int = 0
    warnings: int = 0
    events: int = 0
    instruction_sections: int = 0
    span_days: int = 0
    memory_ids: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.decisions + self.warnings + self.events + self.instruction_sections

    def headline(self) -> str:
        """The line a first-time user sees. It has to be true."""
        if not self.total:
            return "No memories imported — this repository has no history worth seeding yet."

        span = ""
        if self.span_days >= 60:
            span = f" spanning {self.span_days // 30} months"
        elif self.span_days >= 1:
            span = f" spanning {self.span_days} days"

        return (
            f"Imported {self.total} memories from {self.commits_scanned} commits{span}."
        )

    def detail_lines(self) -> list[str]:
        def plural(count: int, singular: str, suffix: str = "s") -> str:
            return f"{count} {singular}{'' if count == 1 else suffix}"

        lines = []
        if self.warnings:
            lines.append(
                f"{plural(self.warnings, 'warning')} "
                "(reverts and repeatedly-fixed files)"
            )
        if self.decisions:
            lines.append(
                f"{plural(self.decisions, 'decision')} "
                "(commits that explain their reasoning)"
            )
        if self.events:
            lines.append(f"{plural(self.events, 'event')} (fixes and features)")
        if self.instruction_sections:
            lines.append(
                f"{plural(self.instruction_sections, 'section')} from instruction files"
            )
        if self.skipped_low_signal:
            lines.append(f"{plural(self.skipped_low_signal, 'low-signal commit')} skipped")
        return lines

    def as_dict(self) -> dict[str, Any]:
        return {
            "commits_scanned": self.commits_scanned,
            "skipped_low_signal": self.skipped_low_signal,
            "decisions": self.decisions,
            "warnings": self.warnings,
            "events": self.events,
            "instruction_sections": self.instruction_sections,
            "span_days": self.span_days,
            "total": self.total,
            "errors": self.errors,
        }


def is_low_signal(message: str) -> bool:
    """Report whether a commit message carries no durable recall value."""
    subject = (message or "").strip().splitlines()[0] if (message or "").strip() else ""
    if not subject:
        return True

    for pattern in _LOW_SIGNAL_PATTERNS:
        if pattern.search(subject):
            return True

    prefix = subject.split(":", 1)[0].strip().lower()
    # Strip a conventional-commit scope, e.g. "chore(deps)".
    prefix = re.sub(r"\(.*\)$", "", prefix).strip()
    if prefix in LOW_SIGNAL_PREFIXES:
        return True

    # A bare subject with no verb-like content is noise regardless of prefix.
    return len(subject) < 12


def _changed_paths(commit) -> list[str]:
    try:
        return [p for p in commit.stats.files.keys() if not _IGNORED_PATH_RE.search(str(p))]
    except Exception:
        return []


def bootstrap_project(
    memory: Any,
    repo_path: Optional[Path] = None,
    *,
    limit: int = DEFAULT_COMMIT_LIMIT,
    include_instructions: bool = True,
) -> BootstrapReport:
    """Seed a memory store from an existing repository.

    Safe to run more than once: commits and instruction sections are content-hashed by
    the existing capture manifest, so re-running updates rather than duplicates.
    """
    report = BootstrapReport()
    repo_path = Path(repo_path or Path.cwd())

    try:
        import git  # noqa: F401  (presence check)

        from visp_memory.capture.git import GitCapture
    except ImportError:
        report.errors.append(
            "git capture unavailable — install with: pip install 'visp-memory[capture]'"
        )
        return report

    try:
        capture = GitCapture(memory, repo_path=repo_path)
    except Exception as exc:
        report.errors.append(f"not a git repository ({exc})")
        return report

    try:
        commits = list(capture.repo.iter_commits(max_count=limit))
    except Exception as exc:
        # A repository with no commits yet raises here; that is not an error worth
        # showing a first-time user as a failure.
        logger.debug("no commits to bootstrap from: %s", exc)
        commits = []

    if commits:
        newest = commits[0].committed_datetime
        oldest = commits[-1].committed_datetime
        report.span_days = max(0, (newest - oldest).days)

    fix_counts: Counter[str] = Counter()
    fix_examples: dict[str, str] = {}
    reverts: list[tuple[str, list[str]]] = []
    rationales: list[tuple[str, str]] = []
    plain_events: list[Any] = []

    for commit in commits:
        if len(commit.parents) > 1:
            continue  # merge commits duplicate their branch's content

        report.commits_scanned += 1
        message = (commit.message or "").strip()
        subject = message.splitlines()[0] if message else ""

        if is_low_signal(message):
            report.skipped_low_signal += 1
            continue

        paths = _changed_paths(commit)

        if _REVERT_RE.search(subject):
            reverts.append((subject, paths))
            continue

        if _FIX_RE.search(subject):
            for path in paths:
                if _NON_CODE_PATH_RE.search(str(path)):
                    continue
                fix_counts[path] += 1
                fix_examples.setdefault(path, subject)

        if _RATIONALE_RE.search(message):
            rationales.append((subject, message))
            continue

        plain_events.append(commit)

    # --- Warnings: reverts -------------------------------------------------------
    for subject, paths in reverts:
        scope = ", ".join(sorted(paths)[:3]) if paths else "the codebase"
        try:
            memory_id = memory.warn(
                area=scope,
                warning=(
                    f"Previously reverted: {subject}. This approach was tried and backed "
                    f"out — check the history before proposing it again."
                ),
                severity=0.8,
                tags=["git"],
                _write_channel=WriteChannel.BOOTSTRAP,
            )
            report.memory_ids.append(memory_id)
            report.warnings += 1
        except Exception as exc:
            report.errors.append(f"revert warning failed: {exc}")

    # --- Warnings: repeatedly fixed files ----------------------------------------
    for path, count in fix_counts.most_common():
        if count < FRAGILE_FILE_MIN_FIXES:
            continue
        try:
            memory_id = memory.warn(
                area=path,
                warning=(
                    f"Historically fragile: {count} separate bug fixes have landed here "
                    f"(e.g. \"{fix_examples.get(path, '')}\"). Change with care and check "
                    f"for regressions."
                ),
                severity=0.7,
                tags=["git"],
                _write_channel=WriteChannel.BOOTSTRAP,
            )
            report.memory_ids.append(memory_id)
            report.warnings += 1
        except Exception as exc:
            report.errors.append(f"fragile-file warning failed: {exc}")

    # --- Decisions: commits that explain themselves ------------------------------
    for subject, message in rationales:
        body = message[len(subject) :].strip()
        reason = body or subject
        try:
            memory_id = memory.decision(
                what=subject,
                why=reason[:1000],
                tags=["git"],
                _write_channel=WriteChannel.BOOTSTRAP,
            )
            report.memory_ids.append(memory_id)
            report.decisions += 1
        except Exception as exc:
            report.errors.append(f"decision capture failed: {exc}")

    # --- Events: the remaining meaningful commits --------------------------------
    for commit in plain_events:
        try:
            memory_id = capture.on_commit(commit.hexsha)
            if memory_id:
                report.memory_ids.append(memory_id)
                report.events += 1
        except Exception as exc:
            report.errors.append(f"commit {commit.hexsha[:7]} failed: {exc}")

    # --- Instruction files --------------------------------------------------------
    if include_instructions:
        try:
            from visp_memory.capture.instructions import ingest_instructions

            ingest = ingest_instructions(memory, root=repo_path)
            report.instruction_sections = ingest.stored
            report.memory_ids.extend(ingest.memory_ids)
        except Exception as exc:
            report.errors.append(f"instruction ingest failed: {exc}")

    return report


def summarize_fragile_files(memory: Any, repo_path: Optional[Path] = None) -> dict[str, int]:
    """Return fix-count-per-file, for reporting without writing memories."""
    counts: dict[str, int] = defaultdict(int)
    try:
        import git

        repo = git.Repo(Path(repo_path or Path.cwd()), search_parent_directories=True)
    except Exception:
        return {}

    for commit in repo.iter_commits(max_count=DEFAULT_COMMIT_LIMIT):
        if len(commit.parents) > 1:
            continue
        subject = (commit.message or "").strip().splitlines()[0:1]
        if not subject or not _FIX_RE.search(subject[0]):
            continue
        for path in _changed_paths(commit):
            counts[path] += 1
    return dict(counts)
