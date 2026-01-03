"""Anchor memories to code structure, and notice when that structure moves.

Flat text retrieval treats a codebase as a bag of sentences. ["Code Isn't
Memory"](https://arxiv.org/abs/2606.22417) tested the alternative directly -- a structural
index inside a coding agent, evaluated on SWE-PolyBench and SWE-bench Pro -- and found a
large localisation gain and a statistically separated resolve gain at no cost penalty,
with lower cost-per-solved than agentic grep.

Two things follow for a memory system, and this module implements both.

**Anchoring.** Most useful memories are *about* something concrete: a file, a module, a
symbol. "WARNING [src/auth/session.py]: cache writes race" is a claim about a specific
path. Extracting that anchor turns a string match into a structural one, so a memory
surfaces when you touch the thing it describes rather than when you happen to use its
vocabulary.

**Staleness.** This is the part general-purpose memory cannot do. A memory anchored to
``src/auth/session.py`` makes an implicit claim that the file exists. When the file is
deleted or renamed, the memory is not merely old -- it is describing something that is no
longer there, and it should stop being injected as though it were current. Code is the
one domain where memory validity can be *checked* rather than guessed at, because the
ground truth is sitting in the working tree.

Nothing here deletes memories. Like the trust layer, staleness bounds injection
eligibility; explicit recall still returns everything.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Optional

ANCHOR_TAG_PREFIX = "anchor:"

# Paths as they appear in memory text: bracketed warnings ("WARNING [src/a.py]"),
# capture summaries ("Modified: src/a.py, src/b.py"), and bare mentions. Requires a
# directory separator or a known source extension so ordinary prose is not mistaken for
# a path.
_PATH_RE = re.compile(
    r"(?<![\w/.-])"
    r"(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+\.[A-Za-z0-9]{1,6}"
    r"|(?<![\w/.-])[A-Za-z0-9_-]+\.(?:py|ts|tsx|js|jsx|go|rs|rb|java|kt|c|h|cc|cpp|"
    r"cs|php|swift|scala|sh|sql|yaml|yml|toml)\b"
)

# Directories that appear in paths but are never worth anchoring to.
_NOISE_RE = re.compile(
    r"(^|/)(node_modules|\.git|\.venv|venv|__pycache__|dist|build|site-packages)(/|$)"
)


class AnchorState(str, Enum):
    """Whether a memory's anchor still corresponds to something real."""

    PRESENT = "present"
    MISSING = "missing"
    UNVERIFIED = "unverified"


@dataclass(frozen=True)
class AnchorReport:
    """Anchors found on a memory and whether they still resolve."""

    anchors: tuple[str, ...] = ()
    present: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    state: AnchorState = AnchorState.UNVERIFIED

    @property
    def anchored(self) -> bool:
        return bool(self.anchors)

    @property
    def fully_stale(self) -> bool:
        """Every anchor is gone, so the memory describes code that no longer exists.

        A partially-stale memory (one of three files deleted) is still about live code
        and stays eligible; only a memory whose entire subject has vanished is withheld.

        Requires an actual MISSING verdict. Inferring staleness from an empty ``present``
        would make every anchored memory look stale whenever the filesystem was not
        checked, silently withholding the most structurally relevant memories in exactly
        the cases where verification was unavailable.
        """
        return self.state is AnchorState.MISSING and bool(self.anchors)

    def as_dict(self) -> dict[str, Any]:
        return {
            "anchors": list(self.anchors),
            "present": list(self.present),
            "missing": list(self.missing),
            "state": self.state.value,
        }


def extract_anchors(content: str) -> tuple[str, ...]:
    """Find file-path anchors in memory text, deduplicated and ordered."""
    if not content:
        return ()

    seen: list[str] = []
    for match in _PATH_RE.finditer(content):
        path = match.group(0).strip(".,;:)]}")
        if not path or _NOISE_RE.search(path):
            continue
        if path not in seen:
            seen.append(path)
    return tuple(seen)


def anchor_tag(path: str) -> str:
    return f"{ANCHOR_TAG_PREFIX}{path}"


def anchors_of(memory: dict[str, Any]) -> tuple[str, ...]:
    """Read anchors from tags when present, else recover them from the content."""
    tagged = [
        str(tag)[len(ANCHOR_TAG_PREFIX) :]
        for tag in (memory.get("tags") or [])
        if str(tag).startswith(ANCHOR_TAG_PREFIX)
    ]
    if tagged:
        return tuple(tagged)
    return extract_anchors(str(memory.get("content", "")))


def _resolves(root: Path, path: str) -> bool:
    candidate = (root / path).resolve()
    try:
        # Reject traversal outside the repository rather than reporting on it.
        candidate.relative_to(root.resolve())
    except ValueError:
        return False
    if candidate.exists():
        return True

    # A path recorded relative to a subdirectory still counts if its tail is unique
    # enough to resolve; this keeps renames of the *root* from marking everything stale.
    tail = Path(path).name
    if len(tail) < 4:
        return False
    try:
        return any(
            match.is_file()
            for match in root.rglob(tail)
            if not _NOISE_RE.search(str(match.relative_to(root)).replace("\\", "/"))
        )
    except OSError:
        return False


def inspect(
    memory: dict[str, Any],
    repo_root: Optional[Path] = None,
) -> AnchorReport:
    """Report a memory's anchors and whether they still exist on disk."""
    anchors = anchors_of(memory)
    if not anchors:
        return AnchorReport()

    if repo_root is None:
        return AnchorReport(anchors=anchors, state=AnchorState.UNVERIFIED)

    root = Path(repo_root)
    present = tuple(path for path in anchors if _resolves(root, path))
    missing = tuple(path for path in anchors if path not in present)

    if present:
        state = AnchorState.PRESENT
    else:
        state = AnchorState.MISSING

    return AnchorReport(anchors=anchors, present=present, missing=missing, state=state)


@dataclass
class AnchorIndex:
    """A path -> memories map, built once per injection rather than per candidate."""

    by_path: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def build(cls, memories: Iterable[dict[str, Any]]) -> "AnchorIndex":
        index = cls()
        for memory in memories:
            memory_id = memory.get("id")
            if not memory_id:
                continue
            for path in anchors_of(memory):
                index.by_path.setdefault(_normalize(path), []).append(memory_id)
        return index

    def for_files(self, files: Iterable[str]) -> set[str]:
        """Memory ids anchored to any of ``files``, matching on path suffix."""
        wanted = {_normalize(str(f)) for f in files}
        hits: set[str] = set()
        for path, ids in self.by_path.items():
            for target in wanted:
                if path == target or path.endswith("/" + target) or target.endswith("/" + path):
                    hits.update(ids)
                    break
        return hits


def _normalize(path: str) -> str:
    return str(path).replace("\\", "/").lstrip("./")


def anchored_to(memory: dict[str, Any], files: Iterable[str]) -> bool:
    """Whether a memory is anchored to any of the given files."""
    if not files:
        return False
    return bool(AnchorIndex.build([{**memory, "id": memory.get("id") or "_"}]).for_files(files))
