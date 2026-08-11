"""Memory's file-grain collapse, checked against intel's conformance vectors.

Two implementations of one rule drift silently. The collapse that turns a consumer
projection into *file A depends on file B* now exists twice -- TypeScript in `visp-kit`,
Python here -- and nothing but these vectors keeps them answering the same question.
A divergence caught here is a failing test in Memory's own CI; a divergence caught
nowhere is a retrieval difference no one can attribute to a cause.

The vectors are vendored under `tests/vectors/file-grain-collapse/`; their provenance,
including the fact that they were copied from an untracked directory and are pinned by
content rather than by a commit, is stated in `VENDORED.md` beside them.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from visp_memory.core.code_graph import (
    PROXIMITY_DECAY,
    GraphState,
    collapse_to_file_graph,
    file_grain_edges,
    load_projection,
)

VECTORS = Path(__file__).resolve().parents[1] / "vectors" / "file-grain-collapse"


def _read(name: str) -> dict:
    return json.loads((VECTORS / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def projection() -> dict:
    return _read("projection.json")


@pytest.fixture(scope="module")
def graph(projection):
    return collapse_to_file_graph(projection)


def _as_edge_list(mapping) -> list[dict]:
    return [{"file": key, "targets": list(values)} for key, values in mapping.items()]


def test_vendored_vectors_match_their_manifest():
    """Content pinning, since no commit id resolves to these bytes.

    This is the check that notices when intel republishes the vectors: the hashes move,
    this fails, and someone re-reads the contract rather than discovering months later
    that the two implementations were conformance-tested against different fixtures.
    """
    manifest = _read("manifest.json")
    documents = dict(manifest["documents"])
    documents["fixture-sources.json"] = manifest["fixtureSources"]

    for name, expected in sorted(documents.items()):
        raw = (VECTORS / name).read_bytes()
        assert len(raw) == expected["bytes"], f"{name} byte length"
        assert hashlib.sha256(raw).hexdigest() == expected["sha256"], f"{name} sha256"

    assert manifest["collapseVersion"] == "1.0"


def test_collapse_matches_the_expected_file_grain(graph):
    expected = _read("expected-file-grain.json")["expected"]

    # Order is part of the contract, so these are list comparisons, not set ones.
    assert list(graph.file_paths) == expected["filePaths"]
    assert list(graph.test_file_paths) == expected["testFilePaths"]
    assert _as_edge_list(graph.internal_edges) == expected["dependencyEdges"]
    assert _as_edge_list(graph.external_edges) == expected["externalDependencies"]
    assert _as_edge_list(graph.test_edges) == expected["testEdges"]


def test_collapse_ordering_is_code_point_not_locale(graph):
    """`Router.ts` before `api.ts`, which locale collation gets backwards.

    The pair is in the fixture for exactly this reason: under `localeCompare` with no
    locale argument the order flips with `LANG`, and any consumer that truncates an
    ordered list then truncates to a different set on a different machine.
    """
    paths = list(graph.file_paths)
    assert paths.index("src/service/Router.ts") < paths.index("src/service/api.ts")
    assert paths == sorted(paths)


def test_self_edges_and_pathless_sources_are_dropped(graph):
    """The barrel re-exports itself and something imports from nowhere; neither shows."""
    for source, targets in graph.internal_edges.items():
        assert source not in targets
    # The barrel's only edges are the self-edge and its own defines/writes rows.
    assert "src/core/barrel.ts" not in graph.internal_edges
    assert "src/core/barrel.ts" in graph.file_paths


def test_a_file_with_no_edges_is_still_a_file(graph):
    assert "src/isolated/orphan.ts" in graph.file_paths
    assert "src/isolated/orphan.ts" not in graph.adjacency()


_CASES = _read("expected-neighbourhoods.json")["cases"]


@pytest.mark.parametrize("case", _CASES, ids=lambda c: c["name"])
def test_neighbourhood_cases(graph, case):
    """Every case, including the four degradation ones, asserted field by field."""
    given = case["input"]
    expected = case["expected"]

    result = graph.neighbourhood(
        given["seeds"],
        max_hops=given["maxHops"],
        max_files=given["maxFiles"],
    )

    assert dict(result.hops) == {row["file"]: row["hop"] for row in expected["hops"]}
    assert list(result.seeds_in_snapshot) == expected["seedsInSnapshot"]
    assert list(result.seeds_not_in_snapshot) == expected["seedsNotInSnapshot"]
    assert [item.seed for item in result.seeds_rejected] == [
        row["seed"] for row in expected["seedsRejected"]
    ]
    assert all(item.reason for item in result.seeds_rejected)
    assert result.truncated is expected["truncated"]
    assert result.reached == expected["reached"]


@pytest.mark.parametrize("case", _CASES, ids=lambda c: c["name"])
def test_consumer_convention_arithmetic(graph, case):
    """`decay**hop` for Memory's own decay, which happens to be the echoed 0.5.

    The echo is arithmetic, not a recommendation. What this pins is that two
    implementations of the same consumer-side convention cannot disagree about the
    multiplication -- the constant itself is asserted to be Memory's, below.
    """
    given = case["input"]
    echo = case["consumerConventionEcho"]["proximity"]
    assert _read("expected-neighbourhoods.json")["consumerConvention"]["decay"] == PROXIMITY_DECAY

    proximity = graph.neighbourhood(
        given["seeds"], max_hops=given["maxHops"], max_files=given["maxFiles"]
    ).proximity()

    assert proximity == {row["file"]: row["proximity"] for row in echo}


def test_a_superseded_snapshot_does_not_load():
    """Intel ships two ids and no verdict; the refusal is Memory's, in Memory's source."""
    head = load_projection(VECTORS / "projection.json")
    superseded = load_projection(VECTORS / "projection-superseded.json")

    assert head.state is GraphState.LOADED
    assert superseded.state is GraphState.NOT_HEAD
    assert superseded.graph is None
    assert "not the repository head" in superseded.reason

    # Byte-identical but for headSnapshotId -- so the refusal really is about currency
    # and not about anything else in the document.
    a = _read("projection.json")
    b = _read("projection-superseded.json")
    assert a["identity"]["headSnapshotId"] != b["identity"]["headSnapshotId"]
    a["identity"]["headSnapshotId"] = b["identity"]["headSnapshotId"]
    assert a == b


def test_the_file_grain_multiset_is_stable(graph):
    """The property intel keeps green on its side, asserted on Memory's.

    A one-off manual check is how the earlier size table went unreproduced for a phase.
    This is the same property -- the `(sourcePath, targetPath, kind)` multiset -- kept
    by a test on this side of the seam too.
    """
    expected = _read("expected-file-grain.json")["expected"]
    edges = file_grain_edges(graph)

    def flatten(block: str, kind: str) -> list[tuple[str, str, str]]:
        return [
            (row["file"], target, kind)
            for row in expected[block]
            for target in row["targets"]
        ]

    derived = sorted(
        flatten("dependencyEdges", "dependency")
        + flatten("testEdges", "test")
        + flatten("externalDependencies", "external")
    )

    assert list(edges) == derived
    assert len(edges) == len(set(edges)), "the collapse must not emit a duplicate edge"
