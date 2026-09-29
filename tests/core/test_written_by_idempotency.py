"""Attribution is informational: it must never decide idempotency or replay."""

import json
from datetime import datetime, timezone

import pytest

from tests.core.test_prohibition_authority import authority  # noqa: F401  (fixture)
from visp_memory.core.attribution import (
    WriterIdentity,
    bind_writer,
    metadata_matches,
    without_written_by,
)
from visp_memory.core.authority import (
    ProhibitionAuthorityError,
    build_prohibition_claim,
    sign_prohibition_attestation,
)
from visp_memory.core.neo4j_governance import Neo4jGovernance
from visp_memory.core.storage import EvidenceImmutableError, LocalStorage

NOW = datetime(2026, 8, 2, 6, tzinfo=timezone.utc)
ALICE = WriterIdentity("alice", "s-1", "cli")
BOB = WriterIdentity("bob", "s-2", "mcp")


@pytest.fixture(autouse=True)
def _no_ambient_writer(monkeypatch):
    monkeypatch.delenv("VISP_MEMORY_AGENT", raising=False)
    monkeypatch.delenv("VISP_MEMORY_SESSION", raising=False)


def test_without_written_by_drops_only_the_stamp():
    assert without_written_by({"a": 1, "written_by": {"agent": "x"}}) == {"a": 1}
    assert without_written_by(None) == {}


@pytest.mark.parametrize(
    ("stored", "incoming", "expected"),
    [
        ('{"a": 1, "written_by": {"agent": "x"}}', {"a": 1, "written_by": {"agent": "y"}}, True),
        ('{"a": 1}', {"a": 1, "written_by": {"agent": "y"}}, True),
        ({"a": 1, "written_by": {"agent": "x"}}, {"a": 1}, True),
        ("", None, True),
        ('{"a": 1}', {"a": 2}, False),
        ('{"a": 1}', {"a": 1, "b": 2}, False),
        ("not json", {"a": 1}, False),
    ],
)
def test_metadata_matches(stored, incoming, expected):
    assert metadata_matches(stored, incoming) is expected


def _stored_written_by(storage, evidence_id):
    return storage.get_evidence(evidence_id)["metadata"].get("written_by")


def test_evidence_retry_under_another_agent_keeps_first_writer(tmp_path):
    storage = LocalStorage(tmp_path)
    with bind_writer(ALICE):
        storage.store_evidence("tool output", "repo-a", evidence_id="ev-1")
    with bind_writer(BOB):
        assert storage.store_evidence("tool output", "repo-a", evidence_id="ev-1") == "ev-1"
    assert storage.store_evidence("tool output", "repo-a", evidence_id="ev-1") == "ev-1"
    assert _stored_written_by(storage, "ev-1") == {
        "agent": "alice",
        "session": "s-1",
        "client": "cli",
    }


def test_evidence_retry_by_agent_after_anonymous_first_write(tmp_path):
    """A row written before attribution existed has no written_by to match."""
    storage = LocalStorage(tmp_path)
    storage.store_evidence("old", "repo-a", evidence_id="ev-old")
    with bind_writer(ALICE):
        assert storage.store_evidence("old", "repo-a", evidence_id="ev-old") == "ev-old"
    assert _stored_written_by(storage, "ev-old") is None


def test_evidence_retry_still_rejects_a_different_payload(tmp_path):
    storage = LocalStorage(tmp_path)
    with bind_writer(ALICE):
        storage.store_evidence("x", "repo-a", metadata={"k": 1}, evidence_id="ev-m")
    with bind_writer(BOB):
        storage.store_evidence("x", "repo-a", metadata={"k": 1}, evidence_id="ev-m")
        with pytest.raises(EvidenceImmutableError):
            storage.store_evidence("x", "repo-a", metadata={"k": 2}, evidence_id="ev-m")
        with pytest.raises(EvidenceImmutableError):
            storage.store_evidence("changed", "repo-a", metadata={"k": 1}, evidence_id="ev-m")
    with pytest.raises(EvidenceImmutableError):
        storage.store_evidence("x", "repo-a", evidence_id="ev-m")


def _signed_prohibition(storage, authority):  # noqa: F811
    content = "Never disable signature checks"
    with bind_writer(ALICE):
        evidence_id = storage.store_evidence(content, "repo-a", evidence_id="ev-p")
    evidence = storage.get_evidence(evidence_id)
    envelope = sign_prohibition_attestation(
        build_prohibition_claim(
            content=content,
            repo_id="repo-a",
            evidence=[{"id": evidence_id, "content_hash": evidence["content_hash"]}],
        ),
        key_id=authority["key_id"],
        private_key=authority["private_key"],
        nonce="nonce-1",
        issued_at=NOW,
    )

    def write(writer, text=content, memory_id="prohibition-1"):
        with bind_writer(writer):
            return storage.store_memory(
                text,
                layer="semantic",
                category="prohibition",
                repo_id="repo-a",
                evidence_ids=[evidence_id],
                authority_attestation=envelope,
                memory_id=memory_id,
                auto_link=False,
            )

    return write


def test_signed_prohibition_replay_from_another_agent_is_accepted(
    tmp_path, authority, monkeypatch  # noqa: F811
):
    monkeypatch.setattr("visp_memory.core.authority.utc_now", lambda: NOW)
    storage = LocalStorage(tmp_path)
    write = _signed_prohibition(storage, authority)

    assert write(ALICE) == "prohibition-1"
    assert write(BOB) == "prohibition-1"
    assert write(None) == "prohibition-1"
    assert storage.get_memory("prohibition-1")["metadata"]["written_by"]["agent"] == "alice"
    with storage._get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM authority_attestations").fetchone()[0] == 1


def test_tampered_prohibition_replay_is_still_rejected(
    tmp_path, authority, monkeypatch  # noqa: F811
):
    monkeypatch.setattr("visp_memory.core.authority.utc_now", lambda: NOW)
    storage = LocalStorage(tmp_path)
    write = _signed_prohibition(storage, authority)
    write(ALICE)

    with pytest.raises(ProhibitionAuthorityError):
        write(BOB, text="Never disable signature checks, ever")
    with pytest.raises(ProhibitionAuthorityError, match="different belief"):
        write(BOB, memory_id="prohibition-2")


class _Row(dict):
    def single(self):
        return self or None


class _EvidenceTx:
    """Answers the one Evidence read the Neo4j comparison makes."""

    def __init__(self, stored):
        self.stored = stored

    def run(self, query, **params):
        return _Row(e=self.stored) if "MATCH (e:Evidence" in query else _Row()


def _neo4j_evidence(metadata, content="c"):
    return {
        "id": "ev-1",
        "content": content,
        "repo_id": "r",
        "evidence_type": "observation",
        "provenance": "unknown",
        "metadata": json.dumps(metadata),
        "content_hash": LocalStorage._evidence_hash(content),
        "created_at": "2026-01-01T00:00:00+00:00",
    }


def test_neo4j_evidence_retry_ignores_attribution_but_not_content():
    stored = _neo4j_evidence({"written_by": {"agent": "alice"}})
    retry = _neo4j_evidence({"written_by": {"agent": "bob"}})
    Neo4jGovernance._insert_graph_evidence(_EvidenceTx(stored), retry)
    Neo4jGovernance._insert_graph_evidence(_EvidenceTx(_neo4j_evidence({})), retry)
    with pytest.raises(EvidenceImmutableError):
        Neo4jGovernance._insert_graph_evidence(
            _EvidenceTx(stored), _neo4j_evidence({"k": 1, "written_by": {"agent": "bob"}})
        )
    with pytest.raises(EvidenceImmutableError):
        Neo4jGovernance._insert_graph_evidence(_EvidenceTx(stored), _neo4j_evidence({}, "d"))


class _ReplayTx:
    """Answers the attestation and Memory reads of Neo4jGovernance._verify_graph_authority."""

    def __init__(self, memory, attestation):
        self.memory, self.attestation = memory, attestation

    def run(self, query, **params):
        if "AuthorityAttestation" in query:
            return _Row(a=self.attestation)
        if "MATCH (m:Memory" in query:
            return _Row(m=self.memory)
        return _Row(e=_neo4j_evidence({}))


def _neo4j_replay(monkeypatch, stored_metadata, incoming_metadata, content="rule"):
    """Run the replay branch of _verify_graph_authority against a stored belief."""
    verified = type("V", (), {"key_id": "k", "nonce": "n", "digest": "d", "envelope": "{}"})
    monkeypatch.setattr(
        "visp_memory.core.authority.verify_prohibition_attestation", lambda *a, **k: verified
    )
    fields = {
        "id": "p-1",
        "content": "rule",
        "layer": "semantic",
        "repo_id": "r",
        "belief_type": "prohibition",
        "epistemic_status": "observed",
        "status": "active",
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    stored = dict(fields, metadata=json.dumps(stored_metadata), evidence_ids=["ev-1"])
    record = dict(fields, content=content, metadata=json.dumps(incoming_metadata))
    tx = _ReplayTx(stored, {"digest": "d", "belief_id": "p-1"})
    return Neo4jGovernance._verify_graph_authority(
        Neo4jGovernance.__new__(Neo4jGovernance), tx, record, ["ev-1"], "{}", None
    )


def test_neo4j_signed_replay_ignores_attribution_but_rejects_tampering(monkeypatch):
    first = {"environment": ["prod"], "written_by": {"agent": "alice"}}
    other = {"environment": ["prod"], "written_by": {"agent": "bob"}}

    assert _neo4j_replay(monkeypatch, first, other) is False
    assert _neo4j_replay(monkeypatch, {"environment": ["prod"]}, other) is False
    with pytest.raises(ProhibitionAuthorityError, match="conflicts"):
        _neo4j_replay(monkeypatch, first, {**other, "environment": ["dev"]})
    with pytest.raises(ProhibitionAuthorityError, match="conflicts"):
        _neo4j_replay(monkeypatch, first, other, content="changed rule")
