import pytest

from tests.core.test_arcadedb_storage import FakeArcadeDbModule
from tests.core.test_neo4j_storage import FakeResult, neo4j_storage_with_delete_count
from visp_memory.core.arcadedb_storage import ArcadeDbStorage
from visp_memory.core.storage import LocalStorage


def _neo4j_storage_with_captured_records():
    storage = neo4j_storage_with_delete_count(0)
    session = storage.driver.session_obj
    session.execute_read = session.execute_write
    evidence_records, memory_records = {}, {}

    def create_evidence(params):
        record = dict(params["record"])
        evidence_records[record["id"]] = record
        return FakeResult()

    def create_memory(params):
        record = dict(params["record"])
        memory_records[record["id"]] = record
        return FakeResult()

    def read_evidence(params):
        record = evidence_records.get(params["id"])
        return FakeResult({"e": record} if record else None)

    def read_memory(params):
        record = memory_records.get(params["id"])
        return FakeResult({"m": record} if record else None)

    def link_evidence(params):
        memory_records[params["id"]]["evidence_ids"] = list(params["evidence_ids"])
        return FakeResult()

    session.query_results.update({
        "CREATE (e:Evidence)": create_evidence,
        "CREATE (m:Memory": create_memory,
        "MATCH (e:Evidence {id: $id}) RETURN e": read_evidence,
        "SET m.evidence_ids = $evidence_ids": link_evidence,
        "MATCH (m:Memory {id: $id})": read_memory,
    })
    return storage


@pytest.fixture(params=["sqlite", "arcadedb", "neo4j"])
def storage(request, tmp_path, monkeypatch):
    if request.param == "sqlite":
        backend = LocalStorage(tmp_path)
    elif request.param == "arcadedb":
        driver = FakeArcadeDbModule()
        monkeypatch.setattr(
            "visp_memory.core.arcadedb_storage.load_arcadedb_driver", lambda: driver
        )
        backend = ArcadeDbStorage(tmp_path)
    else:
        backend = _neo4j_storage_with_captured_records()
    yield backend
    backend.close()


@pytest.mark.parametrize("layer", ["raw", "episodic"])
@pytest.mark.parametrize(
    "ownership",
    [None, {}, {"author_id": "alice"}, {"team_id": "alpha"},
     {"author_id": "alice", "team_id": "alpha"}],
)
def test_automatic_capture_preserves_only_present_memory_ownership(storage, layer, ownership):
    metadata = None if ownership is None else {
        **ownership,
        "files": ["src/login.py"],
        "environment": ["production"],
        "write_channel": "mcp",
    }
    original_metadata = dict(metadata) if metadata is not None else None
    memory_id = storage.store_memory(
        "The login test passed", layer=layer, repo_id="unregistered",
        metadata=metadata, auto_link=False,
    )
    memory = storage.get_memory(memory_id)
    assert len(memory["evidence_ids"]) == 1

    evidence = storage.get_evidence(memory["evidence_ids"][0])

    assert evidence["metadata"] == {
        "captured_memory_id": memory_id,
        "exact_input": True,
        **(ownership or {}),
    }
    assert evidence["content"] == memory["content"]
    assert evidence["repo_id"] == memory["repo_id"]
    assert metadata == original_metadata
