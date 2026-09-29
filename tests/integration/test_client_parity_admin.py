import copy
import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import requests

from visp_memory.core.attribution import WriterIdentity, bind_writer
from visp_memory.core.indexing import EmbeddingIndexReport, ReindexResult, ReindexScope
from visp_memory.core.remote.owner_auth import owner_token_path_for_request
from visp_memory.core.remote_storage import RemoteStorageError

pytest_plugins = ["tests.integration.shared_server"]


def test_client_export_import_preserves_content_and_writer(client_memory, tmp_path):
    source = client_memory("proj-a")
    other = client_memory("proj-b")
    target = client_memory("proj-c")
    # Attribution comes from the bound writer; the client forwards it as headers.
    with bind_writer(WriterIdentity(agent="writer-a", session="s-a")):
        memory_id = source._storage.store_memory(
            "Original portable content", repo_id="proj-a",
            metadata={"written_by": {"agent": "forged"}},
        )
    assert source._storage.peek_memory(memory_id)["metadata"]["written_by"] == {
        "agent": "writer-a", "session": "s-a",
    }
    other.record("Keep project B private")
    export_path = tmp_path / "export.json"
    exported = source.export(export_path)
    assert json.loads(export_path.read_text())["memories"] == exported["memories"]
    assert "Keep project B private" not in json.dumps(exported, default=str)

    with pytest.raises(RemoteStorageError, match="HTTP 422"):
        target.import_memories(export_path)

    # Stable IDs cannot coexist in two repositories. Remove the original, then
    # use the existing fallback-scope convention to move this graph to proj-c.
    assert source._storage.purge_repository("proj-a")["status"] == "purged"
    portable = copy.deepcopy(exported)
    for rows in portable["memories"].values():
        for row in rows:
            row.pop("repo_id", None)
    for row in portable["intents"]:
        row.pop("repo_id", None)
    export_path.write_text(json.dumps(portable, default=str))
    # A different agent imports; the record keeps the writer its export carried.
    with bind_writer(WriterIdentity(agent="importer", session="s-c")):
        target.import_memories(export_path)
    imported = target._storage.peek_memory(memory_id)
    assert imported["repo_id"] == "proj-c"
    assert imported["content"] == "Original portable content"
    assert imported["metadata"]["written_by"] == {"agent": "writer-a", "session": "s-a"}
    assert target._storage.list_evidence(repo_id="proj-c")
    assert other._storage.list_memories(repo_id="proj-b")[0]["content"] == "Keep project B private"


def test_client_admin_methods_use_discovered_owner_token(client_memory, shared_server):
    memory = client_memory("proj-a")
    storage = memory._storage
    token_path = owner_token_path_for_request(shared_server, shared_server)
    assert isinstance(token_path, Path) and token_path.is_file()
    assert token_path.name == f"owner-{urlsplit(shared_server).port}.token"
    memory_id = memory.record("Memory to inspect and purge")
    scope = ReindexScope(repo_id="proj-a")
    inspection = storage.inspect_embedding_index(
        storage_backend="client", provider="noop", scope=scope,
    )
    assert isinstance(inspection, EmbeddingIndexReport)
    assert inspection.storage_backend == "sqlite"
    assert inspection.matched_memories == 1
    before = storage.peek_memory(memory_id)
    result = storage.rebuild_embedding_index(scope=scope, dry_run=True)
    assert isinstance(result, ReindexResult)
    assert result.dry_run is True
    assert result.matched_memories == 1
    assert result.reindexed_memories == 0
    assert storage.peek_memory(memory_id) == before

    assert requests.get(f"{shared_server}/platform/audit-log", timeout=5).status_code == 403
    events = storage.list_audit_logs(repo_id="proj-a", event_type="embedding_reindex.dry_run")
    assert len(events) == 1
    assert events[0]["repo_id"] == "proj-a"
    assert storage.delete_memory(memory_id) is True
    assert storage.purge_memory(memory_id) is True
    assert storage.peek_memory(memory_id) is None
    assert storage.list_audit_logs(repo_id="proj-a", event_type="memory.purged")


def test_client_capabilities_match_shared_server_backend(client_memory, shared_server):
    capabilities = client_memory("proj-a")._storage.get_capabilities()
    response = requests.get(f"{shared_server}/diagnostics/capabilities", timeout=5)
    assert response.status_code == 200
    assert capabilities.to_dict() == response.json()
    assert capabilities.complete_graph_export
    assert capabilities.atomic_graph_import
    assert capabilities.audit_log
    assert capabilities.reindex
    assert not capabilities.vector_search
