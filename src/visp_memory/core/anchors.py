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
    #: Anchors the working tree could not settle either way -- the name exists but the
    #: identity does not, or the tree was too large to walk. Held apart from ``missing``
    #: on purpose: "I could not check" is not "it is gone", and only the second one is
    #: allowed to withhold a memory.
    unverified: tuple[str, ...] = ()

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
            "unverified": list(self.unverified),
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


# A tree bigger than this is not walked. Walking it would cost more than the answer is
# worth, and a truncated walk that reported MISSING would be inventing staleness out of
# its own budget -- so an unresolved anchor against a truncated index is UNVERIFIED.
TREE_INDEX_MAX_FILES = 20_000


@dataclass(frozen=True)
class TreeIndex:
    """Basename -> repository-relative paths, walked once instead of once per anchor.

    ``truncated`` is load-bearing. It says the walk stopped early, which means a
    basename absent from ``by_name`` proves nothing, so every unresolved anchor
    against it must come back UNVERIFIED rather than MISSING.
    """

    root: Path
    by_name: dict[str, tuple[str, ...]] = field(default_factory=dict)
    truncated: bool = False


def build_tree_index(root: Path, *, max_files: int = TREE_INDEX_MAX_FILES) -> TreeIndex:
    """Walk the working tree once so a pool of memories can be checked cheaply."""
    root = Path(root)
    collected: dict[str, list[str]] = {}
    seen = 0
    truncated = False
    try:
        for match in root.rglob("*"):
            try:
                relative = str(match.relative_to(root)).replace("\\", "/")
            except ValueError:
                continue
            if _NOISE_RE.search(relative):
                continue
            if not match.is_file():
                continue
            seen += 1
            if seen > max_files:
                truncated = True
                break
            collected.setdefault(match.name, []).append(relative)
    except OSError:
        # A tree we cannot read is a tree we cannot report on.
        return TreeIndex(root=root, truncated=True)

    return TreeIndex(
        root=root,
        by_name={name: tuple(sorted(paths)) for name, paths in collected.items()},
        truncated=truncated,
    )


def _resolve_state(root: Path, path: str, index: Optional[TreeIndex]) -> AnchorState:
    """Decide PRESENT / MISSING / UNVERIFIED for one recorded path.

    The exact path is the only thing that yields PRESENT outright. Everything else goes
    through a *suffix* match on the whole recorded path, not on its basename.

    The basename fallback this replaces resolved ``src/auth/session.py`` against any
    file anywhere in the tree called ``session.py``. At file grain that reads as
    rename tolerance; it is really a false-PRESENT generator, and the same shape at
    symbol grain (``handler`` exists in fifty files) is fatal. Two rules narrow it:
    the candidate's path must end with the *entire* recorded path, and the match must
    be unique. An ambiguous or name-only hit is UNVERIFIED -- something by that name
    exists, its identity is unconfirmed -- which is what keeps this narrowing from
    manufacturing staleness where the old rule manufactured presence.
    """
    candidate = (root / path).resolve()
    try:
        # Reject traversal outside the repository rather than reporting on it.
        candidate.relative_to(root.resolve())
    except ValueError:
        return AnchorState.MISSING
    if candidate.exists():
        return AnchorState.PRESENT

    normalized = _normalize(path)
    tail = Path(normalized).name
    if len(tail) < 4:
        return AnchorState.MISSING

    if index is None:
        index = build_tree_index(root)

    by_name = index.by_name.get(tail, ())
    if not by_name:
        return AnchorState.UNVERIFIED if index.truncated else AnchorState.MISSING

    suffix_matches = [
        found
        for found in by_name
        if found == normalized or found.endswith("/" + normalized)
    ]
    if len(suffix_matches) == 1:
        return AnchorState.PRESENT

    # Either several files answer to the recorded path, or the only thing the tree
    # agrees on is the name. Neither is a verdict.
    return AnchorState.UNVERIFIED


def inspect(
    memory: dict[str, Any],
    repo_root: Optional[Path] = None,
    *,
    tree_index: Optional[TreeIndex] = None,
) -> AnchorReport:
    """Report a memory's anchors and whether they still exist on disk."""
    anchors = anchors_of(memory)
    if not anchors:
        return AnchorReport()

    if repo_root is None:
        return AnchorReport(anchors=anchors, state=AnchorState.UNVERIFIED)

    root = Path(repo_root)
    if tree_index is not None and Path(tree_index.root) != root:
        tree_index = None

    verdicts = {path: _resolve_state(root, path, tree_index) for path in anchors}
    present = tuple(p for p in anchors if verdicts[p] is AnchorState.PRESENT)
    missing = tuple(p for p in anchors if verdicts[p] is AnchorState.MISSING)
    unverified = tuple(p for p in anchors if verdicts[p] is AnchorState.UNVERIFIED)

    if present:
        state = AnchorState.PRESENT
    elif unverified:
        # Three states, never two: an anchor nobody could settle keeps the memory out
        # of the stale bucket, because withholding it would be acting on an answer the
        # filesystem never gave.
        state = AnchorState.UNVERIFIED
    else:
        state = AnchorState.MISSING

    return AnchorReport(
        anchors=anchors,
        present=present,
        missing=missing,
        unverified=unverified,
        state=state,
    )


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
