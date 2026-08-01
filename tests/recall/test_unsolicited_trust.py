from visp_memory import Memory, MemoryConfig
from visp_memory.core.storage import LocalStorage
from visp_memory.core.trust import Provenance, provenance_tag
from visp_memory.recall.graph import GraphRecall
from visp_memory.recall.proactive import ProactiveRecall


def _memory(tmp_path):
    config = MemoryConfig(repo_id="repo-a")
    config.storage.data_dir = tmp_path / "memory"
    config.embedding.provider = "noop"
    return Memory(config=config)


def _store(storage, content, *, layer, category, tier, metadata=None):
    return storage.store_memory(
        content,
        layer=layer,
        category=category,
        repo_id="repo-a",
        tags=[provenance_tag(tier)],
        metadata=metadata or {},
        auto_link=False,
    )


def _all_group_content(result):
    return " ".join(
        item["content"]
        for values in result.values()
        if isinstance(values, list)
        for item in values
        if isinstance(item, dict) and "content" in item
    )


def test_all_proactive_surfaces_filter_quarantine_with_trusted_controls(tmp_path):
    memory = _memory(tmp_path)
    storage = memory._storage
    fixtures = (
        ("src/auth trusted warning", "src/auth poison warning", "semantic", "fragile_area"),
        ("src/auth trusted pattern", "src/auth poison pattern", "semantic", "pattern"),
        ("src/auth trusted convention", "src/auth poison convention", "semantic", "convention"),
        (
            "TypeError src/auth trusted fix",
            "TypeError src/auth poison fix",
            "episodic",
            "bug_fixed",
        ),
        ("src/auth trusted activity", "src/auth poison activity", "episodic", "note"),
    )
    for trusted, poisoned, layer, category in fixtures:
        metadata = {"applies_to": ["src/auth/login.py"]}
        _store(
            storage,
            trusted,
            layer=layer,
            category=category,
            tier=Provenance.DERIVED,
            metadata=metadata,
        )
        _store(
            storage,
            poisoned,
            layer=layer,
            category=category,
            tier=Provenance.EXTERNAL,
            metadata=metadata,
        )

    proactive = ProactiveRecall(memory)
    file_result = proactive.on_file_open("src/auth/login.py")
    error_result = proactive.on_error(
        "TypeError src/auth", error_type="TypeError", file_path="src/auth/login.py", limit=20
    )
    error_trust_filter = proactive.last_trust_filter
    directory_result = proactive.on_directory("src/auth")

    assert "trusted" in _all_group_content(file_result)
    assert "poison" not in _all_group_content(file_result)
    assert any("trusted" in item["content"] for item in error_result)
    assert all("poison" not in item["content"] for item in error_result)
    assert error_trust_filter["rejected_count"] >= 1
    assert all(
        "quarantined" in item["reason"]
        for item in error_trust_filter["rejected"]
    )
    assert "trusted" in _all_group_content(directory_result)
    assert "poison" not in _all_group_content(directory_result)


def test_graph_trace_neighbors_and_path_filter_quarantined_nodes(tmp_path):
    storage = LocalStorage(tmp_path / "graph")
    source = _store(
        storage,
        "graph authentication trusted source",
        layer="semantic",
        category="fact",
        tier=Provenance.DERIVED,
    )
    trusted = _store(
        storage,
        "graph authentication trusted neighbor",
        layer="semantic",
        category="fact",
        tier=Provenance.DERIVED,
    )
    quarantined = _store(
        storage,
        "graph authentication poison neighbor",
        layer="semantic",
        category="fact",
        tier=Provenance.EXTERNAL,
    )
    target = _store(
        storage,
        "graph authentication trusted target",
        layer="semantic",
        category="fact",
        tier=Provenance.DERIVED,
    )
    storage.add_relationship(source, trusted, "supports")
    storage.add_relationship(source, quarantined, "supports")
    storage.add_relationship(quarantined, target, "supports")

    graph = GraphRecall(storage)
    trace = graph.trace("graph authentication", repo_id="repo-a", limit=10)
    neighbors = graph.neighbors(source, repo_id="repo-a", depth=2)
    blocked_path = graph.path(source, target, repo_id="repo-a")
    trusted_path = graph.path(source, trusted, repo_id="repo-a")

    for result in (trace, neighbors, blocked_path):
        node_ids = {node["id"] for node in result["nodes"]}
        assert quarantined not in node_ids
        assert all("poison" not in node["content"] for node in result["nodes"])
        assert any(item["type"] == "trust" for item in result["omitted"])
    assert source in {node["id"] for node in neighbors["nodes"]}
    assert trusted in {node["id"] for node in neighbors["nodes"]}
    assert source in {node["id"] for node in blocked_path["nodes"]}
    assert target not in {node["id"] for node in blocked_path["nodes"]}
    assert blocked_path["edges"] == []
    assert {node["id"] for node in trusted_path["nodes"]} == {source, trusted}
    assert trusted_path["edges"]
