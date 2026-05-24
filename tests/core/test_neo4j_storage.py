from llm_memory.core.neo4j_storage import Neo4jStorage


class FakeResult:
    def __init__(self, count):
        self.count = count

    def single(self):
        return {"c": self.count}


class FakeSession:
    def __init__(self, result_count):
        self.result_count = result_count
        self.calls = []

    def run(self, query, parameters=None, **params):
        merged_params = dict(parameters or {})
        merged_params.update(params)
        self.calls.append((query, merged_params))
        return FakeResult(self.result_count)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeDriver:
    def __init__(self, result_count):
        self.session_obj = FakeSession(result_count)

    def session(self):
        return self.session_obj


def neo4j_storage_with_delete_count(count):
    storage = Neo4jStorage.__new__(Neo4jStorage)
    storage.driver = FakeDriver(count)
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


def test_neo4j_get_related_memories_rejects_unsafe_relationship_type():
    storage = neo4j_storage_with_delete_count(1)

    try:
        storage.get_related_memories("memory-id", "related`) DETACH DELETE related //")
    except ValueError as exc:
        assert "Relationship type" in str(exc)
    else:
        raise AssertionError("Expected unsafe relationship type to be rejected")

    assert storage.driver.session_obj.calls == []


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
