from llm_memory.core.neo4j_storage import Neo4jStorage


class FakeResult:
    def __init__(self, count, records=None):
        self.count = count
        self.records = records or []

    def single(self):
        return {"c": self.count}

    def __iter__(self):
        return iter(self.records)


class FakeSession:
    def __init__(self, result_count, records=None):
        self.result_count = result_count
        self.records = records or []
        self.calls = []

    def run(self, query, parameters=None, **params):
        merged_params = dict(parameters or {})
        merged_params.update(params)
        self.calls.append((query, merged_params))
        return FakeResult(self.result_count, self.records)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeDriver:
    def __init__(self, result_count, records=None):
        self.session_obj = FakeSession(result_count, records)

    def session(self):
        return self.session_obj


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

    rel_id = storage.add_relationship("source", "target", "depends on")

    assert rel_id
    delete_query, delete_params = storage.driver.session_obj.calls[0]
    assert "[r:RELATED_TO]" in delete_query
    assert delete_params["source_id"] == "source"
    assert delete_params["target_id"] == "target"

    query, params = storage.driver.session_obj.calls[1]
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

    query, params = storage.driver.session_obj.calls[1]
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

    storage.store_memory("vector dimension test", repo_id="repo", auto_link=False)

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
