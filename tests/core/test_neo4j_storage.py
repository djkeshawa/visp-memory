from types import SimpleNamespace

import pytest

from visp_memory.core.neo4j_storage import Neo4jStorage
from visp_memory.core.storage import EvidenceUnsupportedError, StorageMigrationRequired


class FakeResult:
    def __init__(self, single_value=None, records=None):
        self._single = single_value
        self.records = records or []

    def single(self):
        return self._single

    def __iter__(self):
        return iter(self.records)


class FakeSession:
    def __init__(self, result_count, records=None):
        self.result_count = result_count
        self.records = records or []
        self.calls = []
        # Optional overrides keyed by a substring of the Cypher query. Each maps to
        # a callable(params) -> FakeResult so tests can model backend-specific reads
        # (e.g. memory existence lookups for add_relationship validation).
        self.query_results = {}

    def run(self, query, parameters=None, **params):
        merged_params = dict(parameters or {})
        merged_params.update(params)
        self.calls.append((query, merged_params))

        for needle, factory in self.query_results.items():
            if needle in query:
                return factory(merged_params)

        return FakeResult({"c": self.result_count}, self.records)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeDriver:
    def __init__(self, result_count, records=None):
        self.session_obj = FakeSession(result_count, records)
        self.close_calls = 0

    def session(self):
        return self.session_obj

    def verify_connectivity(self):
        return None

    def close(self):
        self.close_calls += 1


class FakeNeo4jDateTime:
    def iso_format(self):
        return "2026-06-06T00:00:00+00:00"


def neo4j_storage_with_delete_count(count, records=None):
    storage = Neo4jStorage.__new__(Neo4jStorage)
    storage.driver = FakeDriver(count, records)
    storage._embedding_fn = None
    storage._embedding_dimension = None
    storage._vector_property = "embedding"
    storage._vector_index = "memory_embedding_index"
    storage._uses_noop_embeddings = False
    return storage


def set_memory_repo_ids(storage, repo_by_id):
    """Make _get_memory_repo_id lookups resolve against ``repo_by_id``.

    ``repo_by_id`` maps memory id -> repo_id. Ids absent from the mapping are treated
    as missing memories (the lookup returns None).
    """

    def factory(params):
        memory_id = params["id"]
        if memory_id not in repo_by_id:
            return FakeResult(single_value=None)
        return FakeResult(single_value={"repo_id": repo_by_id[memory_id]})

    storage.driver.session_obj.query_results["RETURN m.repo_id AS repo_id"] = factory


def relationship_write_calls(storage):
    """Return only the DELETE/MERGE calls, skipping the existence-lookup reads."""
    return [
        (query, params)
        for query, params in storage.driver.session_obj.calls
        if "RETURN m.repo_id AS repo_id" not in query
    ]


def test_neo4j_delete_memory_returns_false_when_missing():
    storage = neo4j_storage_with_delete_count(0)

    assert storage.delete_memory("missing-memory-id") is False


def test_neo4j_delete_memory_returns_true_when_deleted():
    storage = neo4j_storage_with_delete_count(1)

    assert storage.delete_memory("memory-id") is True
    query, params = storage.driver.session_obj.calls[0]
    assert "DETACH DELETE" in query
    assert params == {"id": "memory-id"}


def test_neo4j_add_relationship_rejects_unsafe_relationship_type():
    storage = neo4j_storage_with_delete_count(1)

    try:
        storage.add_relationship("source", "target", "related`) DETACH DELETE b //")
    except ValueError as exc:
        assert "Relationship type" in str(exc)
    else:
        raise AssertionError("Expected unsafe relationship type to be rejected")

    assert storage.driver.session_obj.calls == []


def test_neo4j_add_relationship_normalizes_safe_relationship_type():
    storage = neo4j_storage_with_delete_count(1)
    set_memory_repo_ids(storage, {"source": "repo-a", "target": "repo-a"})

    rel_id = storage.add_relationship("source", "target", "depends on")

    assert rel_id
    write_calls = relationship_write_calls(storage)
    delete_query, delete_params = write_calls[0]
    assert "[r:RELATED_TO]" in delete_query
    assert delete_params["source_id"] == "source"
    assert delete_params["target_id"] == "target"

    query, params = write_calls[1]
    assert "[r:DEPENDS_ON]" in query
    assert params["source_id"] == "source"
    assert params["target_id"] == "target"
    assert params["confidence"] == "ambiguous"
    assert params["confidence_score"] == 1.0
    assert params["source"] == "unspecified"
    assert params["source_file"] is None
    assert params["source_location"] is None
    assert params["reason"] == "Relationship created without evidence metadata."
    assert params["created_by"] is None


def test_neo4j_add_relationship_persists_evidence_metadata():
    storage = neo4j_storage_with_delete_count(1)
    set_memory_repo_ids(storage, {"source": "repo-a", "target": "repo-a"})

    storage.add_relationship(
        "source",
        "target",
        "observed in",
        strength=0.7,
        evidence={
            "confidence": "observed",
            "confidence_score": 0.88,
            "source": "test",
            "source_file": "tests/core/test_neo4j_storage.py",
            "source_location": "test_neo4j_add_relationship_persists_evidence_metadata",
            "reason": "The test fixture directly asserts the edge evidence.",
            "created_by": "pytest",
        },
    )

    query, params = relationship_write_calls(storage)[1]
    assert "[r:OBSERVED_IN]" in query
    assert params["confidence"] == "observed"
    assert params["confidence_score"] == 0.88
    assert params["source"] == "test"
    assert params["source_file"] == "tests/core/test_neo4j_storage.py"
    assert params["source_location"] == "test_neo4j_add_relationship_persists_evidence_metadata"
    assert params["reason"] == "The test fixture directly asserts the edge evidence."
    assert params["created_by"] == "pytest"


def test_neo4j_add_relationship_rejects_unsupported_confidence():
    storage = neo4j_storage_with_delete_count(1)

    try:
        storage.add_relationship("source", "target", "related", evidence={"confidence": "trusted"})
    except ValueError as exc:
        assert "Relationship confidence" in str(exc)
    else:
        raise AssertionError("Expected unsupported confidence to be rejected")

    assert storage.driver.session_obj.calls == []


def test_neo4j_get_related_memories_rejects_unsafe_relationship_type():
    storage = neo4j_storage_with_delete_count(1)

    try:
        storage.get_related_memories("memory-id", "related`) DETACH DELETE related //")
    except ValueError as exc:
        assert "Relationship type" in str(exc)
    else:
        raise AssertionError("Expected unsafe relationship type to be rejected")

    assert storage.driver.session_obj.calls == []


def test_neo4j_get_related_memories_includes_evidence_defaults():
    storage = neo4j_storage_with_delete_count(
        1,
        records=[
            {
                "related": {"id": "target", "content": "Target memory"},
                "rel_type": "RELATED_TO",
                "strength": 0.6,
                "confidence": None,
                "confidence_score": None,
                "source": None,
                "source_file": None,
                "source_location": None,
                "reason": None,
                "created_by": None,
                "created_at": "2026-06-06T00:00:00Z",
            }
        ],
    )

    related = storage.get_related_memories("source")

    assert related[0]["relationship_evidence"] == {
        "confidence": "ambiguous",
        "confidence_score": 0.6,
        "source": "legacy",
        "source_file": None,
        "source_location": None,
        "reason": "Legacy relationship without evidence metadata.",
        "created_by": None,
        "created_at": "2026-06-06T00:00:00Z",
    }


def test_neo4j_get_all_relationships_includes_evidence_metadata():
    storage = neo4j_storage_with_delete_count(
        1,
        records=[
            {
                "id": "rel-1",
                "source": "source",
                "target": "target",
                "type": "RESOLVED_BY",
                "weight": 0.7,
                "confidence": "manual",
                "confidence_score": 2.0,
                "evidence_source": "api",
                "source_file": "docs/example.md",
                "source_location": "L1-L2",
                "reason": "User linked the two memories.",
                "created_by": "alice",
                "created_at": "2026-06-06T00:00:00Z",
            }
        ],
    )

    relationships = storage.get_all_relationships(repo_id="repo-a")

    query, params = storage.driver.session_obj.calls[0]
    assert "a.repo_id = $repo_id" in query
    assert params == {"repo_id": "repo-a"}
    assert relationships == [
        {
            "id": "rel-1",
            "source_id": "source",
            "target_id": "target",
            "relationship": "RESOLVED_BY",
            "strength": 0.7,
            "evidence": {
                "confidence": "manual",
                "confidence_score": 1.0,
                "source": "api",
                "source_file": "docs/example.md",
                "source_location": "L1-L2",
                "reason": "User linked the two memories.",
                "created_by": "alice",
                "created_at": "2026-06-06T00:00:00Z",
            },
        }
    ]


def test_neo4j_get_all_relationships_formats_driver_datetime_evidence():
    storage = neo4j_storage_with_delete_count(
        1,
        records=[
            {
                "id": "rel-1",
                "source": "source",
                "target": "target",
                "type": "RESOLVED_BY",
                "weight": 0.7,
                "confidence": "observed",
                "confidence_score": 0.8,
                "evidence_source": "api",
                "source_file": None,
                "source_location": None,
                "reason": "Driver temporal values should be API serializable.",
                "created_by": "alice",
                "created_at": FakeNeo4jDateTime(),
            }
        ],
    )

    relationship = storage.get_all_relationships(repo_id="repo-a")[0]

    assert relationship["evidence"]["created_at"] == "2026-06-06T00:00:00+00:00"


def test_neo4j_uses_dimension_specific_vector_property_for_memories():
    storage = neo4j_storage_with_delete_count(1)
    storage._embedding_fn = lambda _content: [0.1, 0.2, 0.3]
    storage._embedding_dimension = 3
    storage._vector_property = Neo4jStorage._vector_property_name(3)
    storage._vector_index = Neo4jStorage._vector_index_name(3)

    storage.store_memory(
        "vector dimension test", layer="intent", repo_id="repo", auto_link=False
    )

    query, params = storage.driver.session_obj.calls[0]
    assert "setNodeVectorProperty(m, $vector_property, $embedding)" in query
    assert params["vector_property"] == "embedding_3"
    assert params["embedding"] == [0.1, 0.2, 0.3]


def test_neo4j_dimension_specific_vector_index_names():
    assert Neo4jStorage._vector_property_name(1536) == "embedding_1536"
    assert Neo4jStorage._vector_index_name(1536) == "memory_embedding_index_1536"


def test_neo4j_node_to_dict_strips_dimension_specific_vectors():
    data = Neo4jStorage._node_to_dict(
        {
            "id": "memory-id",
            "content": "Memory",
            "embedding": [1.0],
            "embedding_1536": [2.0],
        }
    )

    assert "embedding" not in data
    assert "embedding_1536" not in data


def test_neo4j_update_memory_refreshes_dimension_specific_vector():
    storage = neo4j_storage_with_delete_count(1)
    storage._embedding_fn = lambda text: [float(len(text)), 0.0]
    storage._embedding_dimension = 2
    storage._vector_property = Neo4jStorage._vector_property_name(2)

    assert storage.update_memory("memory-id", content="updated content") is True

    update_query, update_params = storage.driver.session_obj.calls[0]
    assert "SET m.content = $content" in update_query
    assert update_params["content"] == "updated content"

    vector_query, vector_params = storage.driver.session_obj.calls[1]
    assert "setNodeVectorProperty(m, $vector_property, $embedding)" in vector_query
    assert vector_params["vector_property"] == "embedding_2"
    assert vector_params["embedding"] == [15.0, 0.0]


def test_neo4j_store_memory_rejects_invalid_layer():
    storage = neo4j_storage_with_delete_count(1)

    with pytest.raises(ValueError, match="Memory layer must be one of"):
        storage.store_memory("bad layer", layer="bogus", auto_link=False)

    assert storage.driver.session_obj.calls == []


def test_neo4j_governed_memory_layers_fail_closed_until_evidence_is_supported():
    for layer in ("raw", "episodic", "semantic"):
        storage = neo4j_storage_with_delete_count(1)
        with pytest.raises(EvidenceUnsupportedError, match="Evidence graph"):
            storage.store_memory(
                "governed layer", layer=layer, repo_id="repo-a", auto_link=False
            )
        assert storage.driver.session_obj.calls == []


def test_neo4j_governed_write_accepts_evidence_keyword_before_explicit_refusal():
    storage = neo4j_storage_with_delete_count(1)

    with pytest.raises(EvidenceUnsupportedError, match="Evidence graph"):
        storage.store_memory(
            "Evidence-backed Neo belief",
            layer="semantic",
            repo_id="repo-a",
            evidence_ids=["ev-1"],
            auto_link=False,
        )

    assert storage.driver.session_obj.calls == []


def test_neo4j_markerless_nonempty_constructor_refuses_before_index_or_marker_mutation(
    monkeypatch
):
    driver = FakeDriver(0)
    driver.session_obj.query_results["MATCH (v:SchemaVersion"] = lambda _params: (
        FakeResult(records=[])
    )
    driver.session_obj.query_results["MATCH (n)"] = lambda _params: FakeResult(
        records=[{"present": True}]
    )
    monkeypatch.setattr(
        "visp_memory.core.neo4j_storage.GraphDatabase",
        SimpleNamespace(driver=lambda *_args, **_kwargs: driver),
    )

    with pytest.raises(StorageMigrationRequired, match="[Uu]nversioned"):
        Neo4jStorage(uri="bolt://example", user="neo4j", password="secret")

    mutating = [
        query
        for query, _params in driver.session_obj.calls
        if query.lstrip().startswith(("CREATE", "MERGE", "SET"))
    ]
    assert mutating == []


def test_neo4j_current_marker_memory_node_refuses_before_indexes_and_closes_driver(
    monkeypatch,
):
    driver = FakeDriver(0)
    driver.session_obj.query_results["MATCH (v:SchemaVersion"] = lambda _params: (
        FakeResult(records=[{"version": 4}])
    )
    driver.session_obj.query_results["MATCH (m:Memory)"] = lambda _params: FakeResult(
        records=[{"id": "legacy-memory"}]
    )
    monkeypatch.setattr(
        "visp_memory.core.neo4j_storage.GraphDatabase",
        SimpleNamespace(driver=lambda *_args, **_kwargs: driver),
    )

    with pytest.raises(StorageMigrationRequired, match="Memory nodes"):
        Neo4jStorage(uri="bolt://example", user="neo4j", password="secret")

    mutating = [
        query
        for query, _params in driver.session_obj.calls
        if query.lstrip().startswith(("CREATE", "MERGE", "SET"))
    ]
    assert mutating == []
    assert driver.close_calls == 1


def test_neo4j_current_marker_empty_memory_graph_proceeds_to_indexes(monkeypatch):
    driver = FakeDriver(0)
    driver.session_obj.query_results["MATCH (v:SchemaVersion"] = lambda _params: (
        FakeResult(records=[{"version": 4}])
    )
    driver.session_obj.query_results["MATCH (m:Memory)"] = lambda _params: FakeResult(
        records=[]
    )
    monkeypatch.setattr(
        "visp_memory.core.neo4j_storage.GraphDatabase",
        SimpleNamespace(driver=lambda *_args, **_kwargs: driver),
    )

    storage = Neo4jStorage(uri="bolt://example", user="neo4j", password="secret")

    assert any(query.lstrip().startswith("CREATE") for query, _ in driver.session_obj.calls)
    assert driver.close_calls == 0
    storage.close()
    assert driver.close_calls == 1


def test_neo4j_v3_marker_refuses_before_indexes(monkeypatch):
    driver = FakeDriver(0)
    driver.session_obj.query_results["MATCH (v:SchemaVersion"] = lambda _params: (
        FakeResult(records=[{"version": 3}])
    )
    monkeypatch.setattr(
        "visp_memory.core.neo4j_storage.GraphDatabase",
        SimpleNamespace(driver=lambda *_args, **_kwargs: driver),
    )

    with pytest.raises(StorageMigrationRequired, match="schema migration"):
        Neo4jStorage(uri="bolt://example", user="neo4j", password="secret")

    assert not any(
        query.lstrip().startswith(("CREATE", "MERGE", "SET"))
        for query, _ in driver.session_obj.calls
    )


def test_neo4j_store_memory_accepts_intent_layer():
    storage = neo4j_storage_with_delete_count(1)
    memory_id = storage.store_memory(
        "valid intent", layer="intent", repo_id="repo-a", auto_link=False
    )
    assert memory_id
    assert any("SET m:Intent" in query for query, _ in storage.driver.session_obj.calls)


def test_neo4j_add_relationship_rejects_missing_memory():
    storage = neo4j_storage_with_delete_count(1)
    # Only "source" exists; "target" is absent from the mapping.
    set_memory_repo_ids(storage, {"source": "repo-a"})

    with pytest.raises(ValueError, match="must both exist"):
        storage.add_relationship("source", "target", "related_to")

    # No DELETE/MERGE write should have been issued.
    assert relationship_write_calls(storage) == []


def test_neo4j_add_relationship_rejects_cross_repo_memories():
    storage = neo4j_storage_with_delete_count(1)
    set_memory_repo_ids(storage, {"source": "repo-a", "target": "repo-b"})

    with pytest.raises(ValueError, match="cannot cross repository"):
        storage.add_relationship("source", "target", "related_to")

    assert relationship_write_calls(storage) == []


def test_neo4j_auto_link_skips_invalid_source_ids_without_failing_store():
    # Regression: add_relationship now raises for missing/cross-repo pairs, but a bad
    # source id (e.g. from an import) must not fail the store — the auto-link path skips
    # it, matching the SQLite backend.
    storage = neo4j_storage_with_delete_count(1)
    # The new memory exists; the provenance sources are missing / cross-repo.
    set_memory_repo_ids(storage, {"new-mem": "repo-a", "other-repo-src": "repo-b"})

    storage._auto_link_memory(
        memory_id="new-mem",
        content="hello world",
        repo_id="repo-a",
        source_ids=["ghost-src", "other-repo-src"],
        enabled=False,  # skip the similarity-candidate pass; exercise only source links
    )

    # No relationship was written for either invalid source, and nothing raised.
    assert relationship_write_calls(storage) == []


def test_neo4j_get_stats_includes_memories_by_category():
    storage = neo4j_storage_with_delete_count(4)
    session = storage.driver.session_obj
    session.query_results["RETURN m.layer as layer"] = lambda params: FakeResult(
        records=[
            {"layer": "semantic", "c": 3},
            {"layer": "episodic", "c": 1},
        ]
    )
    session.query_results["RETURN m.category as category"] = lambda params: FakeResult(
        records=[
            {"category": "backend", "c": 2},
            {"category": "general", "c": 2},
        ]
    )

    stats = storage.get_stats()

    assert set(stats.keys()) == {
        "total_memories",
        "memories_by_layer",
        "memories_by_category",
        "active_intents",
        "total_relationships",
    }
    assert stats["memories_by_layer"] == {"semantic": 3, "episodic": 1}
    assert stats["memories_by_category"] == {"backend": 2, "general": 2}
    assert stats["total_memories"] == 4


def test_neo4j_add_team_member_returns_false_when_missing():
    storage = neo4j_storage_with_delete_count(0)

    assert storage.add_team_member("team-a", "missing-user") is False


def test_neo4j_add_team_member_returns_true_when_linked():
    storage = neo4j_storage_with_delete_count(1)

    assert storage.add_team_member("team-a", "alice") is True
    query, _ = storage.driver.session_obj.calls[0]
    assert "MERGE (u)-[r:MEMBER_OF]->(t)" in query
    assert "RETURN count(r) as c" in query


def test_neo4j_search_excludes_raw_layer_by_default():
    storage = neo4j_storage_with_delete_count(0)
    captured = {}

    def factory(params):
        captured["params"] = params
        captured["ran"] = True
        return FakeResult(records=[])

    storage.driver.session_obj.query_results["toLower(m.content) CONTAINS"] = factory

    storage.search_memories(
        "anything",
        environment=["prod"],
        task_type="deploy",
        as_of="2026-01-15T12:00:00+00:00",
    )

    assert captured.get("ran") is True
    query = storage.driver.session_obj.calls[0][0]
    assert "m.layer <> 'raw'" in query
    assert captured["params"]["exclude_raw"] is True
    assert captured["params"]["layer"] is None


def test_neo4j_close_called_via_context_manager():
    storage = neo4j_storage_with_delete_count(0)
    driver = storage.driver

    with storage as ctx:
        assert ctx is storage
        assert driver.close_calls == 0

    # __exit__ delegates to close(), which closes the driver exactly once.
    assert driver.close_calls == 1
    # close() clears the driver reference so the pool can be released.
    assert storage.driver is None


def test_neo4j_double_close_is_safe():
    storage = neo4j_storage_with_delete_count(0)
    driver = storage.driver

    storage.close()
    # A second close() must not raise and must not re-close the already-closed driver.
    storage.close()

    assert driver.close_calls == 1
    assert storage.driver is None


def test_neo4j_intent_status_mutations_are_ineffective():
    storage = neo4j_storage_with_delete_count(1)

    assert storage.complete_intent("intent-1") is False
    assert storage.update_intent("intent-1", status="completed") is False
    assert storage.driver.session_obj.calls == []


def test_neo4j_mixed_intent_update_omits_status():
    storage = neo4j_storage_with_delete_count(1)

    assert storage.update_intent(
        "intent-1", description="Updated description", status="completed"
    ) is True

    query, params = storage.driver.session_obj.calls[0]
    assert "i.description = $description" in query
    assert "status" not in params


def test_neo4j_search_includes_raw_when_layer_requested():
    storage = neo4j_storage_with_delete_count(0)
    captured = {}

    def factory(params):
        captured["params"] = params
        return FakeResult(records=[])

    storage.driver.session_obj.query_results["toLower(m.content) CONTAINS"] = factory

    storage.search_memories("anything", layer="raw")

    assert captured["params"]["exclude_raw"] is False
    assert captured["params"]["layer"] == "raw"
