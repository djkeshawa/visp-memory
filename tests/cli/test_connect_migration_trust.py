"""Migration alone replaces trust while retaining graph validation and identity."""

import base64
import copy
import json
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest
import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tests.cli.test_connect_migration import MigrationRemote, _connect
from visp_memory import Memory, MemoryConfig
from visp_memory.core.authority import (
    PROHIBITION_AUTHORITY_KEYS_ENV,
    build_prohibition_claim,
    sign_prohibition_attestation,
)
from visp_memory.core.memory_import_export import export_storage, import_memory_data
from visp_memory.core.remote_storage import RemoteStorage
from visp_memory.interfaces.connect_migration import migrate_local_records
from visp_memory.interfaces.connect_models import ConnectError


@pytest.fixture
def stores(tmp_path):
    memories = []
    for name in ("source", "target"):
        config = MemoryConfig(repo_id="repo-a")
        config.storage.data_dir = tmp_path / name
        config.embedding.provider = "noop"
        memories.append(Memory(config=config))
    try:
        yield memories
    finally:
        for memory in memories:
            memory.close()


@pytest.fixture
def signed_graph(stores, monkeypatch):
    source, _ = stores
    now = datetime(2026, 8, 2, 6, tzinfo=timezone.utc)
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw,
    )
    monkeypatch.setenv(
        PROHIBITION_AUTHORITY_KEYS_ENV,
        json.dumps({"owner": base64.b64encode(public).decode("ascii")}),
    )
    monkeypatch.setattr("visp_memory.core.authority.utc_now", lambda: now)
    content = "Never bypass owner review"
    evidence_id = source._storage.store_evidence(
        content, repo_id="repo-a", provenance="authored",
    )
    evidence = source._storage.get_evidence(evidence_id)
    claim = build_prohibition_claim(
        content=content, repo_id="repo-a",
        evidence=[{"id": evidence_id, "content_hash": evidence["content_hash"]}],
    )
    envelope = sign_prohibition_attestation(
        claim, key_id="owner", private_key=base64.b64encode(private).decode("ascii"),
        nonce="migration-authority", issued_at=now,
    )
    memory_id = source._storage.store_memory(
        content, layer="semantic", category="prohibition", repo_id="repo-a",
        source="authored", tags=["keep", "provenance:authored"],
        evidence_ids=[evidence_id], authority_attestation=envelope, auto_link=False,
    )
    source._storage.update_memory(
        memory_id, approved_by="owner", approved_at=now.isoformat(),
    )
    return source.export()


@contextmanager
def _remote_to_importer(monkeypatch, target):
    """Exercise the real HTTP client's payload and the server's graph importer."""
    def request(_session, method, url, **kwargs):
        if method == "GET" and url.endswith("/"):
            result = {"status": "online"}
        elif method == "GET":
            assert url.endswith("/repos/repo-a/export")
            result = export_storage(target._storage, "repo-a")
        else:
            assert method == "POST" and url.endswith("/repos/repo-a/import")
            result = import_memory_data(
                target._storage, kwargs["json"], default_repo_id="repo-a",
            )
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps(result, default=str).encode()
        return response

    monkeypatch.setattr(requests.Session, "request", request)
    remote = RemoteStorage(server_url="http://127.0.0.1:9123", repo_id="repo-a")
    try:
        yield remote
    finally:
        remote.close()


@pytest.mark.parametrize("layer", ["raw", "episodic", "semantic", "intent"])
def test_downgrade_is_pure_and_replaces_all_trust_claims(layer):
    from visp_memory.interfaces.connect_migration_trust import downgrade_for_migration

    graph = {
        "memories": {layer: [{
            "id": "memory", "source": "derived",
            "tags": ["keep", "provenance:authored", "provenance:assisted"],
            "approved_by": "owner", "approved_at": "old-approval",
            "metadata": {"write_channel": "cli", "written_by": {"agent": "old"}},
        }]},
        "evidence": [{"id": "evidence", "provenance": "authored", "metadata": {}}],
        "intents": [{"id": "intent", "context": {"keep": True}}],
    }
    before = copy.deepcopy(graph)
    result = downgrade_for_migration(graph)
    row = result["memories"][layer][0]
    assert row["tags"] == ["keep", "provenance:external"]
    assert row["source"] == "external"
    assert row["approved_by"] is None and row["approved_at"] is None
    assert row["metadata"] == {"write_channel": "import", "written_by": {"agent": "old"}}
    assert result["evidence"][0]["provenance"] == "external"
    assert result["evidence"][0]["metadata"]["write_channel"] == "import"
    assert result["intents"] == graph["intents"]
    assert graph == before
    assert downgrade_for_migration(result) == result


def test_signed_graph_migrates_through_client_and_server_validation(
    stores, signed_graph, monkeypatch,
):
    _, target = stores
    original = copy.deepcopy(signed_graph)
    with _remote_to_importer(monkeypatch, target) as remote:
        assert migrate_local_records(signed_graph, "repo-a", remote) == (4, 0)
        assert migrate_local_records(signed_graph, "repo-a", remote) == (0, 4)
    exported = target.export()
    row = exported["memories"]["semantic"][0]
    assert row["tags"] == ["keep", "provenance:external"]
    assert row["source"] == "external"
    assert row["approved_by"] is None and row["approved_at"] is None
    assert exported["evidence"][0]["provenance"] == "external"
    assert exported["evidence"][0]["content_hash"] == original["evidence"][0]["content_hash"]
    assert exported["authority_attestations"] == original["authority_attestations"]
    assert exported["belief_authority"] == original["belief_authority"]
    assert signed_graph == original


@pytest.mark.parametrize("tamper", ["hash", "signature"])
def test_migration_still_refuses_invalid_hash_or_signature(
    stores, signed_graph, monkeypatch, tamper,
):
    _, target = stores
    if tamper == "hash":
        signed_graph["evidence"][0]["content_hash"] = "0" * 64
    else:
        envelope = json.loads(signed_graph["authority_attestations"][0]["envelope"])
        envelope["signature"] = base64.urlsafe_b64encode(b"x" * 64).decode("ascii")
        signed_graph["authority_attestations"][0]["envelope"] = json.dumps(envelope)
    with _remote_to_importer(monkeypatch, target) as remote:
        with pytest.raises(ConnectError, match="invalid hash|signature is invalid"):
            migrate_local_records(signed_graph, "repo-a", remote)
    assert target._storage.list_evidence(repo_id="repo-a") == []
    assert target._storage.list_memories(repo_id="repo-a", status="all") == []


@pytest.mark.parametrize("tier", ["authored", "derived", "assisted"])
def test_memory_and_evidence_arrive_as_external(stores, monkeypatch, tier):
    source, target = stores
    memory_id = source._storage.store_memory(
        "Local observation", repo_id="repo-a", source=tier,
        tags=["keep", f"provenance:{tier}"],
    )
    source._storage.update_memory(memory_id, approved_by="owner")
    graph = source.export()
    with _remote_to_importer(monkeypatch, target) as remote:
        assert migrate_local_records(graph, "repo-a", remote) == (2, 0)
    row = target._storage.peek_memory(memory_id)
    assert row["source"] == "external"
    assert row["tags"] == ["keep", "provenance:external"]
    assert row["approved_by"] is None
    evidence = target._storage.list_evidence(repo_id="repo-a")[0]
    assert evidence["provenance"] == "external"
    assert evidence["metadata"]["write_channel"] == "import"


def test_cli_explains_retained_signed_attestations(stores, signed_graph, tmp_path, monkeypatch):
    source, _ = stores
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.delenv("VISP_MEMORY_CONFIG", raising=False)
    source.config.save(tmp_path / "visp-memory.yaml")
    monkeypatch.setattr("visp_memory.interfaces.connect.RemoteStorage", MigrationRemote)
    MigrationRemote.repositories = {}
    MigrationRemote.imports = []
    MigrationRemote.graph = {}
    result = _connect()
    assert result.exit_code == 0, result.output
    assert "Signed authority attestations retained" in result.output
    assert "signed content, scope and evidence hashes unchanged" in result.output
    assert result.output.index("Trust tiers:") < result.output.index("imported as external")
    assert MigrationRemote.imports[0]["authority_attestations"] == (
        signed_graph["authority_attestations"]
    )


@pytest.mark.parametrize("via_file", [False, True])
def test_non_migration_import_keeps_provenance_approvals_and_authority(
    stores, signed_graph, tmp_path, via_file,
):
    _, target = stores
    if via_file:
        path = tmp_path / "normal-import.json"
        path.write_text(json.dumps(signed_graph, default=str), encoding="utf-8")
        target.import_memories(path)
    else:
        import_memory_data(target._storage, signed_graph, default_repo_id="repo-a")
    exported = target.export()
    for field in ("memories", "evidence", "authority_attestations", "belief_authority"):
        assert exported[field] == signed_graph[field]
