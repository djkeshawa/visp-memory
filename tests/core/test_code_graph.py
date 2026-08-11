"""Reading intel's projection: the degradation states, and the bounds on the walk.

The conformance vectors (`test_code_graph_conformance.py`) pin what the collapse
*answers*. This file pins what happens when there is nothing to collapse, which is the
more common case and the one where a defect is invisible: every failure mode has to
produce the same inert graph, and each has to say which failure it was.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from visp_memory.core.code_graph import (
    EMPTY_GRAPH,
    PROXIMITY_MAX_FILES,
    FileGraph,
    GraphState,
    classify_seed,
    clear_cache,
    collapse_to_file_graph,
    load_projection,
    load_projection_cached,
    normalize_path,
)

VECTORS = Path(__file__).resolve().parents[1] / "vectors" / "file-grain-collapse"


@pytest.fixture(autouse=True)
def _clean_cache():
    clear_cache()
    yield
    clear_cache()


def _valid_projection() -> dict:
    return json.loads((VECTORS / "projection.json").read_text(encoding="utf-8"))


def _write(tmp_path: Path, payload, name: str = "graph.json") -> Path:
    target = tmp_path / name
    target.write_text(
        payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8"
    )
    return target


# --- every failure is the same failure ------------------------------------------------


def test_no_configured_path_is_unconfigured_not_an_error():
    load = load_projection(None)
    assert load.state is GraphState.UNCONFIGURED
    assert load.graph is None


def test_absent_file(tmp_path):
    load = load_projection(tmp_path / "nothing.json")
    assert load.state is GraphState.ABSENT
    assert load.graph is None


def test_oversize_file_is_refused_without_being_parsed(tmp_path, monkeypatch):
    import visp_memory.core.code_graph as code_graph

    monkeypatch.setattr(code_graph, "PROJECTION_MAX_BYTES", 8)
    target = _write(tmp_path, _valid_projection())

    load = code_graph.load_projection(target)

    assert load.state is GraphState.OVERSIZE
    assert load.graph is None


def test_not_json(tmp_path):
    load = load_projection(_write(tmp_path, "{not json"))
    assert load.state is GraphState.MALFORMED
    assert load.graph is None


def test_the_archival_export_is_refused_by_kind(tmp_path):
    """A different artifact with a plausible name is the likeliest wrong file."""
    payload = _valid_projection()
    payload["kind"] = "repository-export"
    load = load_projection(_write(tmp_path, payload))
    assert load.state is GraphState.MALFORMED
    assert "repo export" in load.reason


def test_an_unknown_schema_version_is_refused_rather_than_guessed_at(tmp_path):
    payload = _valid_projection()
    payload["schemaVersion"] = "2.0"
    load = load_projection(_write(tmp_path, payload))
    assert load.state is GraphState.MALFORMED
    assert load.graph is None


def test_identity_without_both_snapshot_ids_cannot_be_judged(tmp_path):
    payload = _valid_projection()
    payload["identity"]["headSnapshotId"] = None
    load = load_projection(_write(tmp_path, payload))
    assert load.state is GraphState.MALFORMED


def test_a_projection_indexing_nothing_is_empty_not_loaded(tmp_path):
    payload = _valid_projection()
    payload["nodes"]["rows"] = []
    payload["edges"]["rows"] = []
    load = load_projection(_write(tmp_path, payload))
    assert load.state is GraphState.EMPTY
    assert load.graph is None


def test_a_missing_table_is_malformed_not_a_crash(tmp_path):
    payload = _valid_projection()
    del payload["edges"]
    load = load_projection(_write(tmp_path, payload))
    assert load.state is GraphState.MALFORMED


def test_every_degradation_yields_the_same_absence(tmp_path):
    """The point of the states is the reason, not the behaviour. The behaviour is one."""
    payload = _valid_projection()
    broken = [
        load_projection(None),
        load_projection(tmp_path / "absent.json"),
        load_projection(_write(tmp_path, "nope", "a.json")),
        load_projection(_write(tmp_path, {**payload, "kind": "other"}, "b.json")),
        load_projection(VECTORS / "projection-superseded.json"),
    ]
    assert {load.graph for load in broken} == {None}
    # Same behaviour, distinguishable reasons -- a silent None is how "structure did
    # nothing" becomes an untestable claim.
    assert len({load.reason for load in broken}) == len(broken)
    for load in broken:
        assert load.reason
        assert load.as_dict()["state"] == load.state.value
        assert "provenance" not in load.as_dict()


def test_the_empty_graph_answers_every_question_inertly():
    assert EMPTY_GRAPH.is_empty
    assert EMPTY_GRAPH.structural_proximity(["src/a.py"]) == {}
    neighbourhood = EMPTY_GRAPH.neighbourhood(["src/a.py"])
    assert neighbourhood.hops == {}
    assert neighbourhood.reached == 0
    assert neighbourhood.answerable is False


# --- spelling -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "given,expected",
    [
        ("src/a.py", "src/a.py"),
        ("./src/a.py", "src/a.py"),
        ("src\\a.py", "src/a.py"),
        ("  src/a.py  ", "src/a.py"),
    ],
)
def test_normalize_accepts_the_spellings_callers_actually_hold(given, expected):
    assert normalize_path(given) == expected


@pytest.mark.parametrize("given", ["", "/etc/passwd", "../outside.py", "C:/x/a.py", "src//a.py"])
def test_normalize_refuses_what_is_not_a_repository_relative_path(given):
    assert normalize_path(given) == ""
    _, reason = classify_seed(given)
    assert reason


def test_there_is_no_basename_fallback():
    """`handler.py` must not resolve to whatever file shares that basename."""
    graph = FileGraph(
        file_paths=("src/a/handler.py", "src/b/handler.py"),
        internal_edges={"src/a/handler.py": ("src/a/util.py",)},
    )
    assert graph.neighbourhood(["handler.py"]).seeds_not_in_snapshot == ("handler.py",)
    assert graph.structural_proximity(["handler.py"]) == {}


# --- the walk -------------------------------------------------------------------------


def _chain(length: int) -> FileGraph:
    paths = tuple(f"src/f{index:03d}.py" for index in range(length))
    edges = {paths[index]: (paths[index + 1],) for index in range(length - 1)}
    return FileGraph(file_paths=paths, internal_edges=edges)


def test_the_walk_is_undirected_over_dependency_and_test_edges():
    graph = FileGraph(
        file_paths=("src/a.py", "src/b.py", "tests/test_b.py"),
        internal_edges={"src/a.py": ("src/b.py",)},
        test_edges={"src/b.py": ("tests/test_b.py",)},
    )
    # From the imported file, both the importer and the test are one hop away.
    assert graph.structural_proximity(["src/b.py"]) == {
        "src/a.py": 0.5,
        "tests/test_b.py": 0.5,
    }


def test_hop_three_is_not_reachable_even_though_it_exists():
    graph = _chain(6)
    scores = graph.structural_proximity(["src/f000.py"])
    assert scores == {"src/f001.py": 0.5, "src/f002.py": 0.25}
    assert "src/f003.py" not in scores


def test_a_hub_cannot_turn_a_recall_into_a_repository_scan():
    spokes = tuple(f"src/spoke{index:04d}.py" for index in range(PROXIMITY_MAX_FILES + 50))
    graph = FileGraph(
        file_paths=("src/hub.py",) + spokes,
        internal_edges={"src/hub.py": spokes},
    )
    result = graph.neighbourhood(["src/hub.py"])
    assert len(result.hops) == PROXIMITY_MAX_FILES + 1  # the seed, plus the bound
    assert result.truncated is True
    assert result.reached == PROXIMITY_MAX_FILES


def test_the_walk_is_deterministic_under_seed_and_edge_reordering():
    forward = FileGraph(
        file_paths=("src/a.py", "src/b.py", "src/c.py"),
        internal_edges={"src/a.py": ("src/b.py", "src/c.py")},
    )
    reversed_edges = FileGraph(
        file_paths=("src/c.py", "src/b.py", "src/a.py"),
        internal_edges={"src/a.py": ("src/c.py", "src/b.py")},
    )
    assert forward.structural_proximity(["src/a.py"]) == reversed_edges.structural_proximity(
        ["src/a.py"]
    )
    assert forward.structural_proximity(["src/a.py", "src/a.py"]) == forward.structural_proximity(
        ["./src/a.py"]
    )


def test_a_seed_the_snapshot_never_saw_is_not_a_file_without_neighbours():
    """The two states intel warns must never collapse into one."""
    graph = FileGraph(
        file_paths=("src/known.py", "src/lonely.py"),
        internal_edges={"src/known.py": ("src/lonely.py",)},
    )

    unknown = graph.neighbourhood(["docs/architecture.md"])
    assert unknown.seeds_not_in_snapshot == ("docs/architecture.md",)
    assert unknown.answerable is False

    graph_without_edges = FileGraph(file_paths=("src/lonely.py",))
    isolated = graph_without_edges.neighbourhood(["src/lonely.py"])
    assert isolated.seeds_in_snapshot == ("src/lonely.py",)
    assert isolated.seeds_not_in_snapshot == ()
    assert isolated.answerable is True
    assert isolated.reached == 0


def test_provenance_names_the_snapshot_a_structural_claim_rests_on():
    graph = collapse_to_file_graph(_valid_projection())
    provenance = graph.provenance()
    assert provenance["snapshot_id"].startswith("urn:visp-intel:snapshot:")
    assert provenance["snapshot_id"] == provenance["head_snapshot_id"]
    assert provenance["repository_instance_id"]


# --- caching --------------------------------------------------------------------------


def test_the_cache_re_reads_an_edited_projection(tmp_path):
    payload = _valid_projection()
    target = _write(tmp_path, payload)
    assert load_projection_cached(target).state is GraphState.LOADED

    payload["nodes"]["rows"] = []
    target.write_text(json.dumps(payload), encoding="utf-8")
    # mtime granularity is not something to rely on; the size changed, and so does the key.
    assert load_projection_cached(target).state is GraphState.EMPTY


def test_the_cache_does_not_grow_without_bound(tmp_path):
    import visp_memory.core.code_graph as code_graph

    for index in range(code_graph._CACHE_LIMIT * 3):
        load_projection_cached(_write(tmp_path, _valid_projection(), f"g{index}.json"))

    assert len(code_graph._cache) <= code_graph._CACHE_LIMIT
