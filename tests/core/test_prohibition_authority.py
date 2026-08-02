"""Ed25519 authority contract for governed prohibition beliefs."""

import base64
import json
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from visp_memory.config import MemoryConfig
from visp_memory.core.authority import (
    PROHIBITION_AUTHORITY_KEYS_ENV,
    ProhibitionAuthorityError,
    build_prohibition_claim,
    sign_prohibition_attestation,
)
from visp_memory.core.storage import LocalStorage

NOW = datetime(2026, 8, 2, 6, tzinfo=timezone.utc)


@pytest.fixture
def authority(monkeypatch):
    private_key = Ed25519PrivateKey.generate()
    private_raw = private_key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    public_raw = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    monkeypatch.setenv(
        PROHIBITION_AUTHORITY_KEYS_ENV,
        json.dumps({"owner-2026": base64.b64encode(public_raw).decode("ascii")}),
    )
    return {
        "key_id": "owner-2026",
        "private_key": base64.b64encode(private_raw).decode("ascii"),
    }


def _attested_write(storage, authority, *, content="Never disable signature checks", **kwargs):
    repo_id = kwargs.pop("repo_id", "repo-a")
    evidence_id = kwargs.pop("evidence_id", None) or storage.store_evidence(
        content, repo_id=repo_id
    )
    evidence = storage.get_evidence(evidence_id)
    metadata = kwargs.pop("metadata", {})
    claim = build_prohibition_claim(
        content=content,
        repo_id=repo_id,
        environment=metadata.get("environment"),
        task_type=metadata.get("task_type"),
        valid_from=metadata.get("valid_from"),
        valid_to=metadata.get("valid_to"),
        evidence=[{"id": evidence_id, "content_hash": evidence["content_hash"]}],
    )
    envelope = sign_prohibition_attestation(
        claim,
        key_id=authority["key_id"],
        private_key=authority["private_key"],
        nonce=kwargs.pop("nonce", "nonce-1"),
        issued_at=kwargs.pop("issued_at", NOW),
        not_before=kwargs.pop("not_before", None),
    )
    memory_id = kwargs.pop("memory_id", "prohibition-1")
    result = storage.store_memory(
        content,
        layer="semantic",
        category="prohibition",
        repo_id=repo_id,
        metadata=metadata,
        evidence_ids=[evidence_id],
        authority_attestation=envelope,
        memory_id=memory_id,
        auto_link=False,
        **kwargs,
    )
    return result, envelope, evidence_id


def test_valid_offline_signed_prohibition_persists_observed_attestation(
    tmp_path, authority, monkeypatch
):
    monkeypatch.setattr("visp_memory.core.authority.utc_now", lambda: NOW)
    storage = LocalStorage(tmp_path)

    memory_id, envelope, evidence_id = _attested_write(storage, authority)

    belief = storage.get_memory(memory_id)
    attestation = storage.get_authority_attestation(memory_id)
    assert belief["belief_type"] == "prohibition"
    assert belief["epistemic_status"] == "observed"
    assert belief["evidence_ids"] == [evidence_id]
    assert attestation["belief_id"] == memory_id
    assert attestation["envelope"] == envelope
    assert attestation["key_id"] == authority["key_id"]


@pytest.mark.parametrize("source", ["assisted", "external", "authored", "admin"])
def test_provenance_or_admin_like_identity_never_substitutes_for_attestation(
    tmp_path, source
):
    storage = LocalStorage(tmp_path)
    content = f"Unsigned {source} rule"
    evidence_id = storage.store_evidence(content, repo_id="repo-a")

    with pytest.raises(ProhibitionAuthorityError, match="attestation"):
        storage.store_memory(
            content,
            layer="semantic",
            category="prohibition",
            repo_id="repo-a",
            source=source,
            metadata={"is_admin": True, "approved_by": "admin-user"},
            evidence_ids=[evidence_id],
            auto_link=False,
        )

    assert storage.list_memories(repo_id="repo-a", status="all") == []


def test_prohibition_refuses_without_matching_runtime_public_key(
    tmp_path, authority, monkeypatch
):
    storage = LocalStorage(tmp_path)
    monkeypatch.setattr("visp_memory.core.authority.utc_now", lambda: NOW)
    monkeypatch.delenv(PROHIBITION_AUTHORITY_KEYS_ENV)

    with pytest.raises(ProhibitionAuthorityError, match="public key"):
        _attested_write(storage, authority)


def test_prohibition_refuses_when_ed25519_support_is_unavailable(
    tmp_path, authority, monkeypatch
):
    storage = LocalStorage(tmp_path)
    monkeypatch.setattr("visp_memory.core.authority.utc_now", lambda: NOW)
    monkeypatch.setattr("visp_memory.core.authority.ED25519_AVAILABLE", False)

    with pytest.raises(ProhibitionAuthorityError, match="Ed25519 support"):
        _attested_write(storage, authority)


@pytest.mark.parametrize("defect", ["malformed", "duplicate", "unknown_field"])
def test_malformed_or_open_envelope_is_rejected_atomically(
    tmp_path, authority, monkeypatch, defect
):
    monkeypatch.setattr("visp_memory.core.authority.utc_now", lambda: NOW)
    storage = LocalStorage(tmp_path)
    content = "Never bypass review"
    evidence_id = storage.store_evidence(content, repo_id="repo-a")
    evidence = storage.get_evidence(evidence_id)
    claim = build_prohibition_claim(
        content=content,
        repo_id="repo-a",
        evidence=[{"id": evidence_id, "content_hash": evidence["content_hash"]}],
    )
    envelope = sign_prohibition_attestation(
        claim,
        key_id=authority["key_id"],
        private_key=authority["private_key"],
        nonce="malformed-nonce",
        issued_at=NOW,
    )
    if defect == "malformed":
        envelope = "{not-json"
    elif defect == "duplicate":
        envelope = envelope[:-1] + ',"key_id":"duplicate"}'
    else:
        parsed = json.loads(envelope)
        parsed["authority"] = "self-claimed"
        envelope = json.dumps(parsed)

    with pytest.raises(ProhibitionAuthorityError):
        storage.store_memory(
            content,
            layer="semantic",
            category="prohibition",
            repo_id="repo-a",
            evidence_ids=[evidence_id],
            authority_attestation=envelope,
            auto_link=False,
        )

    assert storage.list_memories(repo_id="repo-a", status="all") == []


@pytest.mark.parametrize(
    "mutation",
    ["content", "content_hash", "repo_id", "environment", "evidence_id", "evidence_hash"],
)
def test_signed_claim_must_match_redacted_belief_scope_and_evidence(
    tmp_path, authority, monkeypatch, mutation
):
    monkeypatch.setattr("visp_memory.core.authority.utc_now", lambda: NOW)
    storage = LocalStorage(tmp_path)
    content = "Never disable authorization"
    evidence_id = storage.store_evidence(content, repo_id="repo-a")
    evidence = storage.get_evidence(evidence_id)
    metadata = {"environment": ["prod"], "task_type": ["deploy"]}
    claim = build_prohibition_claim(
        content=content,
        repo_id="repo-a",
        environment=["prod"],
        task_type=["deploy"],
        evidence=[{"id": evidence_id, "content_hash": evidence["content_hash"]}],
    )
    if mutation == "content":
        claim["content"] = "Different rule"
    elif mutation == "content_hash":
        claim["content_hash"] = "0" * 64
    elif mutation == "repo_id":
        claim["repo_id"] = "repo-b"
    elif mutation == "environment":
        claim["environment"] = ["dev"]
    elif mutation == "evidence_id":
        claim["evidence"][0]["id"] = "missing"
    else:
        claim["evidence"][0]["content_hash"] = "f" * 64
    envelope = sign_prohibition_attestation(
        claim,
        key_id=authority["key_id"],
        private_key=authority["private_key"],
        nonce=f"mismatch-{mutation}",
        issued_at=NOW,
    )

    with pytest.raises(ProhibitionAuthorityError, match="claim"):
        storage.store_memory(
            content,
            layer="semantic",
            category="prohibition",
            repo_id="repo-a",
            metadata=metadata,
            evidence_ids=[evidence_id],
            authority_attestation=envelope,
            auto_link=False,
        )


def test_wrong_key_or_tampered_signature_is_rejected(tmp_path, authority, monkeypatch):
    monkeypatch.setattr("visp_memory.core.authority.utc_now", lambda: NOW)
    storage = LocalStorage(tmp_path)
    _memory_id, envelope, evidence_id = _attested_write(
        storage, authority, memory_id="valid-first", nonce="valid-first"
    )
    storage.delete_memory("valid-first")
    parsed = json.loads(envelope)
    parsed["signature"] = ("A" if parsed["signature"][0] != "A" else "B") + parsed[
        "signature"
    ][1:]

    with pytest.raises(ProhibitionAuthorityError, match="signature"):
        storage.store_memory(
            "Never disable signature checks",
            layer="semantic",
            category="prohibition",
            repo_id="repo-a",
            evidence_ids=[evidence_id],
            authority_attestation=json.dumps(parsed),
            memory_id="tampered",
            auto_link=False,
        )


def test_redaction_after_signing_and_future_issuance_are_rejected(
    tmp_path, authority, monkeypatch
):
    monkeypatch.setattr("visp_memory.core.authority.utc_now", lambda: NOW)
    storage = LocalStorage(tmp_path)
    secret = "sk-proj-abcdefghijklmnopqrstuvwxyz123456"
    content = f"Never expose {secret}"
    evidence_id = storage.store_evidence("Safe evidence", repo_id="repo-a")
    evidence = storage.get_evidence(evidence_id)
    claim = build_prohibition_claim(
        content=content,
        repo_id="repo-a",
        evidence=[{"id": evidence_id, "content_hash": evidence["content_hash"]}],
    )
    envelope = sign_prohibition_attestation(
        claim,
        key_id=authority["key_id"],
        private_key=authority["private_key"],
        nonce="redaction",
        issued_at=NOW,
    )
    with pytest.raises(ProhibitionAuthorityError, match="claim"):
        storage.store_memory(
            content,
            layer="semantic",
            category="prohibition",
            repo_id="repo-a",
            evidence_ids=[evidence_id],
            authority_attestation=envelope,
            auto_link=False,
        )

    future = sign_prohibition_attestation(
        build_prohibition_claim(
            content="Never bypass release review",
            repo_id="repo-a",
            evidence=[{"id": evidence_id, "content_hash": evidence["content_hash"]}],
        ),
        key_id=authority["key_id"],
        private_key=authority["private_key"],
        nonce="future",
        issued_at=NOW + timedelta(minutes=6),
    )
    with pytest.raises(ProhibitionAuthorityError, match="future"):
        storage.store_memory(
            "Never bypass release review",
            layer="semantic",
            category="prohibition",
            repo_id="repo-a",
            evidence_ids=[evidence_id],
            authority_attestation=future,
            auto_link=False,
        )


def test_attestation_replay_is_idempotent_only_for_same_digest_and_belief(
    tmp_path, authority, monkeypatch
):
    monkeypatch.setattr("visp_memory.core.authority.utc_now", lambda: NOW)
    storage = LocalStorage(tmp_path)
    memory_id, envelope, evidence_id = _attested_write(storage, authority)

    assert storage.store_memory(
        "Never disable signature checks",
        layer="semantic",
        category="prohibition",
        repo_id="repo-a",
        evidence_ids=[evidence_id],
        authority_attestation=envelope,
        memory_id=memory_id,
        auto_link=False,
    ) == memory_id
    with storage._get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM authority_attestations").fetchone()[0] == 1

    changed = sign_prohibition_attestation(
        build_prohibition_claim(
            content="Never disable signature checks",
            repo_id="repo-a",
            evidence=[
                {
                    "id": evidence_id,
                    "content_hash": storage.get_evidence(evidence_id)["content_hash"],
                }
            ],
        ),
        key_id=authority["key_id"],
        private_key=authority["private_key"],
        nonce="nonce-1",
        issued_at=NOW - timedelta(seconds=1),
    )
    with pytest.raises(ProhibitionAuthorityError, match="nonce"):
        storage.store_memory(
            "Never disable signature checks",
            layer="semantic",
            category="prohibition",
            repo_id="repo-a",
            evidence_ids=[evidence_id],
            authority_attestation=changed,
            memory_id="replay-other",
            auto_link=False,
        )


def test_private_key_has_no_runtime_environment_or_config_surface():
    dumped = json.dumps(MemoryConfig().model_dump(), default=str).casefold()
    assert "prohibition_authority_private" not in dumped
    assert PROHIBITION_AUTHORITY_KEYS_ENV.endswith("_KEYS")
