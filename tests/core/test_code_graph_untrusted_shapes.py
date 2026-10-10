"""load_projection documents "never raises": hostile JSON shapes must read as MALFORMED
or be skipped, never escape as TypeError/RecursionError."""

import json

from visp_memory.core.code_graph import GraphState, load_projection


def _projection(**dictionaries):
    payload = {
        "kind": "consumer-graph-projection",
        "schemaVersion": "1.0",
        "identity": {"snapshotId": "s", "headSnapshotId": "s"},
        "dictionaries": {
            "paths": ["a.py", "b.py"], "nodeKinds": ["file"], "edgeKinds": ["imports"],
        },
        "nodes": {"columns": ["path", "kind", "name"], "rows": [[0, 0, "a"], [1, 0, "b"]]},
        "edges": {"columns": ["source", "target", "kind"], "rows": [[0, 1, 0]]},
    }
    payload["dictionaries"].update(dictionaries)
    return payload


def _load(tmp_path, payload):
    target = tmp_path / "projection.json"
    target.write_text(payload if isinstance(payload, str) else json.dumps(payload))
    return load_projection(target)


def test_unhashable_node_kind_is_treated_as_no_kind(tmp_path):
    load = _load(tmp_path, _projection(nodeKinds=[["test"]]))

    assert load.state is GraphState.LOADED
    assert load.graph.file_paths == ("a.py", "b.py")
    assert load.graph.test_file_paths == ()


def test_unhashable_edge_kind_drops_only_that_edge(tmp_path):
    payload = _projection(edgeKinds=[{"x": 1}, "imports"])
    payload["edges"]["rows"] = [[0, 1, 0], [1, 0, 1]]

    load = _load(tmp_path, payload)

    assert load.state is GraphState.LOADED
    assert load.graph.internal_edges == {"b.py": ("a.py",)}


def test_deeply_nested_json_is_malformed_not_a_recursion_error(tmp_path):
    load = _load(tmp_path, "[" * 100000 + "]" * 100000)

    assert load.state is GraphState.MALFORMED
