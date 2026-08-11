"""Read-only structural facts about the repository, from intel's consumer projection.

Memory recalls by text. A memory recorded against ``core/ranking.py`` -- a decision, a
failed approach, a gotcha -- scores exactly zero for a task editing
``core/hybrid_retrieval.py``, which imports it, unless the words happen to overlap.
Both of Memory's file-aware tests are *identity* tests
(``HybridRetriever._matches_entities``, ``Memory._file_factor``), and identity is zero
at one structural hop. This module supplies the missing scale: **proximity**, keyed to
where a task sits in the dependency structure rather than to what the task says.

**Where the facts come from, and what they are allowed to do.** Intel states facts about
the tree at a named snapshot; it carries no readiness flag, no staleness boolean, no
relevance score and no budget. Every threshold that turns one of those facts into a
retrieval decision is a literal in *this* file, visible in Memory's own diff:
:data:`PROXIMITY_MAX_HOPS`, :data:`PROXIMITY_DECAY`, :data:`PROXIMITY_MAX_FILES`,
:data:`PROXIMITY_MIN_ADMISSION`. Read Memory's source alone and you can answer what any
recall would have returned with no graph: the same thing, minus the additions this file
bounds.

**Nothing is stored.** The projection is intel's, derived on demand and never a second
source of truth; copying its rows into Memory's store would create one, would go stale
silently on every commit, and would need snapshot machinery Memory does not have.
Memory reads the artifact, in process, per configured path, and writes nothing.

**Every failure is the same failure.** Unconfigured, absent, oversized, unreadable,
malformed, built against a snapshot that was not the head, or indexing no files at all:
all of them yield no graph, and no graph yields today's behaviour exactly. There is no
path in which a defect in this file can *remove* a memory from a result.

**Snapshot-scoped facts are UNVERIFIED by default.** A proximity edge is true of the
snapshot named in :attr:`FileGraph.snapshot_id`, not of the working tree, and this
module never claims otherwise: it exposes :meth:`FileGraph.provenance` so anything that
surfaces a structurally-admitted memory can say which snapshot vouched for it. Nothing
here reports a file as PRESENT -- that question belongs to :mod:`visp_memory.core.anchors`,
which checks the filesystem, and an entity id from a dead snapshot has no ground truth
to check against at all.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

# ---------------------------------------------------------------------------
# The shared collapse + proximity spec. These constants are frozen: the same
# numbers are pinned in visp-kit's TypeScript implementation, and conformance
# vectors -- not agreement between two authors -- are what hold them together.
# ---------------------------------------------------------------------------

#: The artifact kind intel stamps on a consumer projection.
PROJECTION_KIND = "consumer-graph-projection"

#: Schema versions this reader accepts. A projection announcing anything else is
#: refused rather than guessed at.
SUPPORTED_SCHEMA_VERSIONS = frozenset({"1.0"})

#: Intel's own encoded bound (``GRAPH_PROJECTION_MAX_ENCODED_BYTES``), and therefore
#: Kit's read limit. A file above it is not a projection intel produced, and parsing
#: an unbounded file is an unbounded allocation inside a recall.
PROJECTION_MAX_BYTES = 16 * 1024 * 1024

#: Edge kinds that mean "this file depends on that file".
DEPENDENCY_EDGE_KINDS = frozenset({"imports", "depends_on"})

#: Edge kinds that mean "this file is exercised by that file".
TEST_EDGE_KINDS = frozenset({"tested_by", "covered_by"})

#: Node kinds whose presence makes the containing file a test file.
TEST_NODE_KINDS = frozenset({"test", "fixture"})

#: Breadth-first expansion depth. Hop 3 was not measured and is not enabled.
PROXIMITY_MAX_HOPS = 2

#: Per-hop decay: seed 1.0, hop 1 -> 0.5, hop 2 -> 0.25.
PROXIMITY_DECAY = 0.5

#: Hard cap on how many files one expansion may name, so a hub file cannot turn a
#: recall into a repository scan.
PROXIMITY_MAX_FILES = 256

#: The floor below which proximity admits nothing. Equal to the hop-2 score, so hop 2
#: is the last hop that can admit and hop 3 could not even if it were computed.
PROXIMITY_MIN_ADMISSION = 0.25


class GraphState(str, Enum):
    """Why a read produced a graph, or did not.

    Every non-``LOADED`` state degrades to identical behaviour. They are distinguished
    because a human debugging "why did structure do nothing" needs the reason, and
    because a silent ``None`` is how an untestable claim gets made.
    """

    LOADED = "loaded"
    UNCONFIGURED = "unconfigured"
    ABSENT = "absent"
    OVERSIZE = "oversize"
    UNREADABLE = "unreadable"
    MALFORMED = "malformed"
    NOT_HEAD = "not_head"
    EMPTY = "empty"


class ProjectionError(ValueError):
    """The bytes on disk are not a consumer projection this reader can collapse."""


_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")


def normalize_path(value: Any) -> str:
    """POSIX-normalize a repository-relative path, or return ``""`` if it is not one.

    The one spelling rule, per the collapse contract: convert ``\\`` to ``/``, strip one
    leading ``./``, normalize to NFC. Anything absolute, escaping, or empty is not a
    repository-relative path and comes back empty -- callers that need the reason use
    :func:`classify_seed`.

    There is no basename fallback here and there must never be one. Resolving
    ``handler.ts`` to whichever file happens to share that basename is how false edges
    get built, and this graph conditions what a model is told.
    """
    return classify_seed(value)[0]


def classify_seed(value: Any) -> tuple[str, str]:
    """``(normalized_path, rejection_reason)``. Exactly one of the two is non-empty.

    Kept separate from :func:`normalize_path` because a seed nobody can spell is a
    different fact from a seed the snapshot has never seen, and folding the two together
    is how "I could not look" gets reported as "there is nothing there".
    """
    text = unicodedata.normalize("NFC", str(value or "").replace("\\", "/").strip())
    if text.startswith("./"):
        text = text[2:]
    if not text:
        return "", 'not a portable repository-relative path: ""'
    if text.startswith("/") or _WINDOWS_DRIVE_RE.match(text):
        return "", f"not a portable repository-relative path: {text!r}"
    segments = text.split("/")
    if any(segment in ("", ".", "..") for segment in segments):
        return "", f"escapes or is not canonical: {text!r}"
    return text, ""


@dataclass(frozen=True)
class SeedRejection:
    """A seed that is not a repository-relative portable path, and why."""

    seed: str
    reason: str


@dataclass(frozen=True)
class Neighbourhood:
    """What is structurally near a set of seeds, with the seeds' own states reported.

    Three seed states, never two, for the same reason
    :mod:`visp_memory.core.anchors` carries three anchor states: **in snapshot**,
    **not in snapshot**, and **rejected**. A seed the snapshot has never heard of is
    not a file with no neighbours -- the snapshot may predate the file, or the indexer
    may not have reached it -- and a consumer that folds those together is asserting
    something intel did not say. :attr:`truncated` is the fourth thing that must not be
    silent: a short answer stopped by a bound is not a small neighbourhood.

    :attr:`hops` holds integer hop counts including the seeds at 0, because a hop count
    is a fact about the tree. The weighting is Memory's judgement and lives in
    :meth:`proximity`, whose constants are literals in this file.
    """

    hops: Mapping[str, int] = field(default_factory=dict)
    seeds_in_snapshot: tuple[str, ...] = ()
    seeds_not_in_snapshot: tuple[str, ...] = ()
    seeds_rejected: tuple[SeedRejection, ...] = ()
    truncated: bool = False

    @property
    def reached(self) -> int:
        """How many non-seed files the walk named."""
        return sum(1 for hop in self.hops.values() if hop > 0)

    @property
    def answerable(self) -> bool:
        """Whether any seed named a file this snapshot knows.

        False means the walk had nothing to stand on. It is deliberately distinct from
        an empty :attr:`hops`, which can also mean "this file genuinely has no
        neighbours" -- a fact, where the first is an absence of one.
        """
        return bool(self.seeds_in_snapshot)

    def proximity(self, decay: float = PROXIMITY_DECAY) -> dict[str, float]:
        """``file -> decay**hop`` for non-seed files at or above the admission floor.

        Seeds are excluded: the caller already knows them by identity, and the whole
        point of this map is the files identity cannot reach.
        """
        scores = {}
        for path, hop in self.hops.items():
            if hop <= 0:
                continue
            score = decay**hop
            if score >= PROXIMITY_MIN_ADMISSION:
                scores[path] = score
        return scores

    def as_dict(self) -> dict[str, Any]:
        return {
            "hops": dict(sorted(self.hops.items())),
            "seeds_in_snapshot": list(self.seeds_in_snapshot),
            "seeds_not_in_snapshot": list(self.seeds_not_in_snapshot),
            "seeds_rejected": [
                {"seed": item.seed, "reason": item.reason} for item in self.seeds_rejected
            ],
            "truncated": self.truncated,
            "reached": self.reached,
        }


@dataclass(frozen=True)
class FileGraph:
    """Intel's projection collapsed onto the file grain, plus bounded expansion over it.

    Ordering is by Unicode code point (Python's default ``sorted``), never by locale.
    Kit's TypeScript collapse currently orders with ``localeCompare`` and no explicit
    locale, which reads the environment's ICU collation -- two implementations of one
    spec cannot conform while one of them depends on ``LANG``. This side is pinned;
    the divergence is recorded in the conformance vectors' README.
    """

    repository_instance_id: str = ""
    snapshot_id: str = ""
    head_snapshot_id: str = ""
    #: Repository-relative source paths intel indexed, sorted.
    file_paths: tuple[str, ...] = ()
    #: Paths holding at least one ``test`` or ``fixture`` entity, sorted.
    test_file_paths: tuple[str, ...] = ()
    #: file -> files it imports or depends on.
    internal_edges: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    #: file -> external module names it imports.
    external_edges: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    #: file -> files that test or cover it.
    test_edges: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return not self.file_paths

    def provenance(self) -> dict[str, str]:
        """What a surfaced structural claim must be labelled with.

        A memory admitted because of an import edge is being surfaced on the authority
        of a snapshot, not of the working tree. If it arrives unlabelled it is a
        remembered claim about code that may have moved -- the confident wrong answer
        this whole layer exists to prevent.
        """
        return {
            "repository_instance_id": self.repository_instance_id,
            "snapshot_id": self.snapshot_id,
            "head_snapshot_id": self.head_snapshot_id,
        }

    def adjacency(self) -> dict[str, tuple[str, ...]]:
        """The undirected union of dependency and test edges."""
        merged: dict[str, set[str]] = {}
        for edges in (self.internal_edges, self.test_edges):
            for source, targets in edges.items():
                for target in targets:
                    if source == target:
                        continue
                    merged.setdefault(source, set()).add(target)
                    merged.setdefault(target, set()).add(source)
        return {node: tuple(sorted(neighbours)) for node, neighbours in sorted(merged.items())}

    def neighbourhood(
        self,
        seeds: Iterable[str],
        *,
        max_hops: int = PROXIMITY_MAX_HOPS,
        max_files: int = PROXIMITY_MAX_FILES,
    ) -> Neighbourhood:
        """Bounded breadth-first walk over the undirected dependency ∪ test edges.

        Deterministic: seeds are classified in code-point order, expansion proceeds hop
        by hop with ties broken by code point, and truncation keeps the prefix of that
        same order -- so the answer does not depend on which machine asked. Ordering is
        by Unicode code point, never by locale collation, which is a conformance
        requirement rather than a preference: a locale-ordered walk truncates to a
        different set under a different ``LANG``.

        Unusable seeds are reported, not fatal; one bad seed in a call never abandons
        the good ones beside it.
        """
        known = set(self.file_paths)
        in_snapshot: set[str] = set()
        not_in_snapshot: set[str] = set()
        rejected: list[SeedRejection] = []
        for seed in seeds:
            path, reason = classify_seed(seed)
            if reason:
                rejected.append(SeedRejection(seed=str(seed or ""), reason=reason))
            elif path in known:
                in_snapshot.add(path)
            else:
                not_in_snapshot.add(path)

        base = Neighbourhood(
            seeds_in_snapshot=tuple(sorted(in_snapshot)),
            seeds_not_in_snapshot=tuple(sorted(not_in_snapshot)),
            seeds_rejected=tuple(sorted(rejected, key=lambda item: item.seed)),
        )
        if not in_snapshot:
            return base

        adjacency = self.adjacency()
        hops: dict[str, int] = {path: 0 for path in sorted(in_snapshot)}
        frontier = sorted(in_snapshot)
        admitted = 0
        truncated = False

        for hop in range(1, max(0, int(max_hops)) + 1):
            next_frontier: list[str] = []
            for node in frontier:
                for neighbour in adjacency.get(node, ()):
                    if neighbour in hops:
                        continue
                    hops[neighbour] = hop
                    next_frontier.append(neighbour)
            if not next_frontier:
                break
            kept: list[str] = []
            for neighbour in sorted(next_frontier):
                if admitted >= max_files:
                    # Bound hit. Everything past here is dropped from the answer and
                    # the answer says so, so a short walk is never read as a small
                    # neighbourhood.
                    truncated = True
                    del hops[neighbour]
                    continue
                admitted += 1
                kept.append(neighbour)
            if truncated:
                break
            frontier = kept

        return Neighbourhood(
            hops=dict(sorted(hops.items())),
            seeds_in_snapshot=base.seeds_in_snapshot,
            seeds_not_in_snapshot=base.seeds_not_in_snapshot,
            seeds_rejected=base.seeds_rejected,
            truncated=truncated,
        )

    def structural_proximity(
        self,
        seeds: Iterable[str],
        *,
        max_hops: int = PROXIMITY_MAX_HOPS,
        decay: float = PROXIMITY_DECAY,
        max_files: int = PROXIMITY_MAX_FILES,
    ) -> dict[str, float]:
        """``file -> (0, 1]`` for non-seed files within ``max_hops`` of ``seeds``.

        The weighted view of :meth:`neighbourhood`. The decay is Memory's judgement and
        its constants are literals in this module; intel states hop counts and stops.
        """
        return self.neighbourhood(
            seeds, max_hops=max_hops, max_files=max_files
        ).proximity(decay)


#: The inert graph. Consumers may hold this instead of ``None`` and every call answers
#: exactly as it would with no projection on disk.
EMPTY_GRAPH = FileGraph()


@dataclass(frozen=True)
class GraphLoad:
    """A read attempt, with its reason. ``graph`` is ``None`` unless state is LOADED."""

    state: GraphState
    reason: str
    graph: Optional[FileGraph] = None
    path: Optional[str] = None

    def as_dict(self) -> dict[str, Any]:
        record: dict[str, Any] = {
            "state": self.state.value,
            "reason": self.reason,
            "path": self.path,
        }
        if self.graph is not None:
            record["provenance"] = self.graph.provenance()
            record["files"] = len(self.graph.file_paths)
        return record


def _column(columns: Any, name: str) -> int:
    if not isinstance(columns, list):
        raise ProjectionError("table columns must be a list")
    try:
        return columns.index(name)
    except ValueError as error:
        raise ProjectionError(f"projection table is missing the {name!r} column") from error


def _cell_index(row: Any, position: int) -> Optional[int]:
    if not isinstance(row, list) or position >= len(row):
        return None
    value = row[position]
    # `null` means absent, in every column, everywhere. Booleans are ints in Python and
    # are not row indices.
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _table(projection: Mapping[str, Any], name: str) -> tuple[list[str], list[Any]]:
    table = projection.get(name)
    if not isinstance(table, dict):
        raise ProjectionError(f"projection is missing the {name!r} table")
    columns = table.get("columns")
    rows = table.get("rows")
    if not isinstance(columns, list) or not isinstance(rows, list):
        raise ProjectionError(f"projection table {name!r} is not {{columns, rows}}")
    return columns, rows


def _external_name(value: Any) -> str:
    # Intel elides a name that is itself an identifier to null, and names an external
    # symbol by module and member; Memory wants the module.
    if not isinstance(value, str):
        return ""
    return value.split("#")[0].strip()


def collapse_to_file_graph(projection: Mapping[str, Any]) -> FileGraph:
    """Collapse a consumer projection onto the file grain.

    Every join in here is on ROW INDEX. Intel's contract states that a node ``name`` is
    display only and that two entities named ``handler`` in two files are two
    identities. Joining on a name is how false edges get built, and this graph is about
    to condition what a model is told.

    The rules, stated so the TypeScript and Python copies can be checked against one
    text rather than against each other:

    1. A node with a ``path`` index contributes that path (POSIX-normalized) to
       ``file_paths``; a node whose kind is ``test`` or ``fixture`` also contributes it
       to ``test_file_paths``. A node without a path is an external or unresolved
       symbol and contributes only its module name.
    2. An edge counts only if its kind is in :data:`DEPENDENCY_EDGE_KINDS` (internal and
       external dependency edges) or :data:`TEST_EDGE_KINDS` (test edges). All other
       kinds are dropped.
    3. An edge whose *source* has no path is dropped: a file-grain edge needs a file to
       hang off.
    4. A dependency edge whose target has no path becomes an external edge naming the
       module; a test edge whose target has no path is dropped, because an external
       module is not a test.
    5. A self edge -- source path equal to target path -- is dropped. It is the
       ``contains`` relation seen from the wrong side once entities collapse onto files.
    6. Confidence, completeness and modality are carried by every edge and are *not*
       consulted here. Filtering on them would be Memory deciding which of intel's
       facts count; proximity is a ranking signal, and a low-confidence edge that
       nudges a memory two ranks is not worth a threshold nobody measured.
    """
    node_columns, node_rows = _table(projection, "nodes")
    edge_columns, edge_rows = _table(projection, "edges")
    dictionaries = projection.get("dictionaries")
    if not isinstance(dictionaries, dict):
        raise ProjectionError("projection is missing its dictionaries")

    paths = dictionaries.get("paths")
    node_kinds = dictionaries.get("nodeKinds")
    edge_kinds = dictionaries.get("edgeKinds")
    for name, value in (("paths", paths), ("nodeKinds", node_kinds), ("edgeKinds", edge_kinds)):
        if not isinstance(value, list):
            raise ProjectionError(f"projection dictionary {name!r} is missing or not a list")

    node_path_column = _column(node_columns, "path")
    node_kind_column = _column(node_columns, "kind")
    node_name_column = _column(node_columns, "name")
    edge_source_column = _column(edge_columns, "source")
    edge_target_column = _column(edge_columns, "target")
    edge_kind_column = _column(edge_columns, "kind")

    file_paths: set[str] = set()
    test_file_paths: set[str] = set()
    path_by_row: dict[int, str] = {}
    external_by_row: dict[int, str] = {}

    for index, row in enumerate(node_rows):
        path_code = _cell_index(row, node_path_column)
        kind_code = _cell_index(row, node_kind_column)
        kind = (
            node_kinds[kind_code]
            if kind_code is not None and 0 <= kind_code < len(node_kinds)
            else None
        )

        if path_code is not None:
            if not 0 <= path_code < len(paths):
                continue
            file_path = normalize_path(paths[path_code])
            if not file_path:
                continue
            path_by_row[index] = file_path
            file_paths.add(file_path)
            if kind in TEST_NODE_KINDS:
                test_file_paths.add(file_path)
            continue

        has_name = isinstance(row, list) and node_name_column < len(row)
        name = _external_name(row[node_name_column] if has_name else None)
        if name:
            external_by_row[index] = name

    internal: dict[str, set[str]] = {}
    external: dict[str, set[str]] = {}
    tests: dict[str, set[str]] = {}

    for row in edge_rows:
        kind_code = _cell_index(row, edge_kind_column)
        if kind_code is None or not 0 <= kind_code < len(edge_kinds):
            continue
        kind = edge_kinds[kind_code]
        is_dependency = kind in DEPENDENCY_EDGE_KINDS
        is_test = kind in TEST_EDGE_KINDS
        if not is_dependency and not is_test:
            continue

        source_row = _cell_index(row, edge_source_column)
        target_row = _cell_index(row, edge_target_column)
        if source_row is None or target_row is None:
            continue

        source = path_by_row.get(source_row)
        if source is None:
            continue

        target = path_by_row.get(target_row)
        if target is not None:
            if target == source:
                continue
            bucket = tests if is_test else internal
            bucket.setdefault(source, set()).add(target)
            continue

        if is_test:
            continue

        module = external_by_row.get(target_row)
        if module:
            external.setdefault(source, set()).add(module)

    identity = projection.get("identity")
    identity = identity if isinstance(identity, dict) else {}

    return FileGraph(
        repository_instance_id=str(identity.get("repositoryInstanceId") or ""),
        snapshot_id=str(identity.get("snapshotId") or ""),
        head_snapshot_id=str(identity.get("headSnapshotId") or ""),
        file_paths=tuple(sorted(file_paths)),
        test_file_paths=tuple(sorted(test_file_paths)),
        internal_edges=_sorted_map(internal),
        external_edges=_sorted_map(external),
        test_edges=_sorted_map(tests),
    )


def _sorted_map(source: Mapping[str, set[str]]) -> dict[str, tuple[str, ...]]:
    return {key: tuple(sorted(values)) for key, values in sorted(source.items())}


def file_grain_edges(graph: FileGraph) -> tuple[tuple[str, str, str], ...]:
    """The ``(sourcePath, targetPath, kind)`` multiset, for conformance checking.

    ``kind`` here is the collapsed kind -- ``dependency``, ``test`` or ``external`` --
    because collapsing is exactly what erases which of ``imports``/``depends_on``
    produced a given file-grain edge.
    """
    rows: list[tuple[str, str, str]] = []
    for source, targets in graph.internal_edges.items():
        rows.extend((source, target, "dependency") for target in targets)
    for source, targets in graph.test_edges.items():
        rows.extend((source, target, "test") for target in targets)
    for source, targets in graph.external_edges.items():
        rows.extend((source, target, "external") for target in targets)
    return tuple(sorted(rows))


def load_projection(path: Optional[Path]) -> GraphLoad:
    """Read and collapse a projection. Never raises; every failure means "no graph"."""
    if path is None:
        return GraphLoad(GraphState.UNCONFIGURED, "no intel projection path is configured")

    resolved = Path(path)
    try:
        info = resolved.stat()
    except OSError:
        return GraphLoad(
            GraphState.ABSENT,
            f"no intel projection at {resolved}",
            path=str(resolved),
        )

    if info.st_size > PROJECTION_MAX_BYTES:
        return GraphLoad(
            GraphState.OVERSIZE,
            (
                f"intel projection is {info.st_size} bytes, above the "
                f"{PROJECTION_MAX_BYTES}-byte read limit"
            ),
            path=str(resolved),
        )

    try:
        raw = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as error:
        return GraphLoad(
            GraphState.UNREADABLE, f"intel projection is unreadable: {error}", path=str(resolved)
        )
    except json.JSONDecodeError as error:
        return GraphLoad(
            GraphState.MALFORMED, f"intel projection is not JSON: {error}", path=str(resolved)
        )

    if not isinstance(raw, dict):
        return GraphLoad(
            GraphState.MALFORMED, "intel projection is not a JSON object", path=str(resolved)
        )

    kind = str(raw.get("kind") or "")
    if kind != PROJECTION_KIND:
        return GraphLoad(
            GraphState.MALFORMED,
            (
                f"artifact kind is {kind!r}, not {PROJECTION_KIND!r}; the archival "
                "`repo export` is a different artifact"
            ),
            path=str(resolved),
        )

    schema_version = str(raw.get("schemaVersion") or "")
    if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        return GraphLoad(
            GraphState.MALFORMED,
            f"projection schemaVersion {schema_version!r} is not one this reader collapses",
            path=str(resolved),
        )

    identity = raw.get("identity")
    identity = identity if isinstance(identity, dict) else {}
    snapshot_id = str(identity.get("snapshotId") or "")
    head_snapshot_id = str(identity.get("headSnapshotId") or "")
    if not snapshot_id or not head_snapshot_id:
        return GraphLoad(
            GraphState.MALFORMED,
            "projection identity does not state both snapshotId and headSnapshotId",
            path=str(resolved),
        )

    # Currency is Memory's call, exactly as it is Kit's: intel states both ids and
    # carries no stale flag. The judgement is narrow and worth stating as such -- rows
    # material in a snapshot that was not the head describe a tree that is not this
    # one. It does NOT detect a projection older than the working tree; both ids move
    # together when the repository is re-indexed, and nothing in the artifact says when
    # that was.
    if snapshot_id != head_snapshot_id:
        return GraphLoad(
            GraphState.NOT_HEAD,
            "projection describes a snapshot that was not the repository head when it was built",
            path=str(resolved),
        )

    try:
        graph = collapse_to_file_graph(raw)
    except ProjectionError as error:
        return GraphLoad(GraphState.MALFORMED, str(error), path=str(resolved))

    if graph.is_empty:
        return GraphLoad(
            GraphState.EMPTY,
            "projection indexed no files in its snapshot",
            path=str(resolved),
        )

    return GraphLoad(
        GraphState.LOADED,
        f"collapsed {len(graph.file_paths)} files from the consumer projection",
        graph=graph,
        path=str(resolved),
    )


# --- in-process cache -------------------------------------------------------
# Keyed by (path, mtime_ns, size) so an edited projection is re-read and a stable one
# is parsed once. Bounded, because a cache that grows with the number of repositories
# a long-lived server has seen is a leak.

_CACHE_LIMIT = 4
_cache: dict[tuple[str, int, int], GraphLoad] = {}


def load_projection_cached(path: Optional[Path]) -> GraphLoad:
    """:func:`load_projection`, memoized on the file's identity and mtime."""
    if path is None:
        return load_projection(None)

    resolved = Path(path)
    try:
        info = resolved.stat()
    except OSError:
        return load_projection(resolved)

    key = (str(resolved), info.st_mtime_ns, info.st_size)
    cached = _cache.get(key)
    if cached is not None:
        return cached

    loaded = load_projection(resolved)
    if len(_cache) >= _CACHE_LIMIT:
        _cache.clear()
    _cache[key] = loaded
    return loaded


def clear_cache() -> None:
    """Drop the in-process projection cache. For tests and long-lived processes."""
    _cache.clear()


def load_for_repo(config: Any, repo_id: Optional[str] = None) -> GraphLoad:
    """The projection configured for one repository, read and collapsed. Never raises.

    The single resolution point, so that every caller that wants a graph asks the same
    question of the same configuration and a reader can find them all by their calls to
    this function.
    """
    section = getattr(config, "code_graph", None)
    if section is None:
        return GraphLoad(GraphState.UNCONFIGURED, "this configuration has no code_graph section")
    return load_projection_cached(section.projection_path_for(repo_id))


def graph_for_repo(config: Any, repo_id: Optional[str] = None) -> Optional[FileGraph]:
    """:func:`load_for_repo`, discarding the reason. For call sites that only branch."""
    return load_for_repo(config, repo_id).graph
