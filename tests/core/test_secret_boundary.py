"""Regression tests for the secret-safe write and belief-revision boundaries."""

from __future__ import annotations

from typing import Any

import pytest

from visp_memory import Memory, MemoryConfig
from visp_memory.core.storage import (
    EvidenceReferenceError,
    LocalStorage,
    SemanticMemoryImmutableError,
)
from visp_memory.quality.conflict import ConflictVerdict
from visp_memory.quality.secrets import SecretBearingContentError

SECRET = "sk-ant-api03-abcdefghijklmnopqrstuvwxyz"


def _memory(tmp_path):
    config = MemoryConfig(repo_id="repo-a")
    config.storage.data_dir = tmp_path
    config.embedding.provider = "noop"
    return Memory(config=config)


def test_learn_redacts_before_evidence_search_and_conflict(tmp_path, monkeypatch):
    memory = _memory(tmp_path)
    raw = f"The deployment key is {SECRET}."
    observed: dict[str, list[str]] = {"evidence": [], "search": [], "conflict": []}

    original_store_evidence = memory._storage.store_evidence

    def capture_evidence(content: str, **kwargs: Any):
        observed["evidence"].append(content)
        return original_store_evidence(content, **kwargs)

    def capture_search(*, query: str, **kwargs: Any):
        observed["search"].append(query)
        return []

    monkeypatch.setattr(memory._storage, "store_evidence", capture_evidence)
    monkeypatch.setattr(memory._storage, "search_memories", capture_search)
    monkeypatch.setattr(
        memory.conflict_detector,
        "detect_conflicts",
        lambda content, relevant: observed["conflict"].append(content)
        or ConflictVerdict.clear(),
    )

    memory_id = memory.learn(raw, detect_conflicts=True, reconcile=True)

    assert memory_id
    for values in observed.values():
        assert values
        assert all(SECRET not in value for value in values)
    stored = memory._storage.get_memory(memory_id)
    assert SECRET not in stored["content"]
    assert "secret_redacted" in stored["quality_flags"]


def test_authority_attested_secret_input_is_rejected_before_evidence_write(
    tmp_path, monkeypatch
):
    memory = _memory(tmp_path)
    called = False

    def fail_if_called(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("secret-bearing authority input reached evidence storage")

    monkeypatch.setattr(memory._storage, "store_evidence", fail_if_called)

    with pytest.raises(SecretBearingContentError):
        memory.learn(
            f"Never publish {SECRET}",
            category="prohibition",
            authority_attestation="opaque-attestation",
        )

    assert called is False


def test_semantic_content_update_is_immutable_and_revision_creates_successor(tmp_path):
    storage = LocalStorage(tmp_path)
    old_evidence = storage.store_evidence("Observed old behavior", repo_id="repo-a")
    old_id = storage.store_memory(
        "The service uses the old endpoint",
        layer="semantic",
        category="fact",
        repo_id="repo-a",
        evidence_ids=[old_evidence],
        metadata={"applies_to": ["service.py"]},
        auto_link=False,
    )

    with pytest.raises(SemanticMemoryImmutableError):
        storage.update_memory(old_id, content="The service uses the new endpoint")

    assert storage.get_memory(old_id)["content"] == "The service uses the old endpoint"

    with pytest.raises(EvidenceReferenceError, match="new evidence"):
        storage.revise_memory(
            old_id,
            "The service uses the new endpoint",
            evidence_ids=[old_evidence],
        )

    new_evidence = storage.store_evidence("Observed new behavior", repo_id="repo-a")
    successor_id = storage.revise_memory(
        old_id,
        "The service uses the new endpoint",
        evidence_ids=[new_evidence],
        reason="The endpoint changed in the latest deployment",
    )

    assert successor_id != old_id
    successor = storage.get_memory(successor_id)
    assert successor["content"] == "The service uses the new endpoint"
    assert successor["evidence_ids"] == [new_evidence]
    assert successor["metadata"]["revision_of"] == old_id

    old = storage.get_memory(old_id)
    assert old["status"] == "superseded"
    assert old["metadata"]["superseded_by"] == successor_id
    relationships = storage.get_all_relationships(repo_id="repo-a")
    assert any(
        relationship["source_id"] == successor_id
        and relationship["target_id"] == old_id
        and relationship["relationship"] == "supersedes"
        for relationship in relationships
    )


def test_reconciled_source_episode_carries_its_evidence_to_successor(tmp_path):
    memory = _memory(tmp_path)
    episode_id = memory.record("Observed the registry manifest during deployment")
    first = memory.learn(
        "Pin container image digests in the deploy pipeline",
        source_episodes=[episode_id],
    )

    successor = memory.learn(
        "Pin container image digests in the deploy pipeline and verify them against "
        "the registry manifest",
        source_episodes=[episode_id],
    )

    assert successor != first
    episode = memory._storage.peek_memory(episode_id)
    revised = memory._storage.peek_memory(successor)
    assert set(episode["evidence_ids"]).issubset(revised["evidence_ids"])


def test_nonsemantic_update_redacts_content_and_records_flags(tmp_path):
    storage = LocalStorage(tmp_path)
    memory_id = storage.store_memory("Old event", repo_id="repo-a", auto_link=False)

    assert storage.update_memory(memory_id, content=f"Updated with {SECRET}") is True

    updated = storage.get_memory(memory_id)
    assert SECRET not in updated["content"]
    assert "secret_redacted" in updated["quality_flags"]
