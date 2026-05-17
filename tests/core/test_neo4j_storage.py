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

    def run(self, query, **params):
        self.calls.append((query, params))
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
    query, params = storage.driver.session_obj.calls[0]
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
