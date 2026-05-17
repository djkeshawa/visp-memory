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
