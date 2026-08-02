"""Governed semantic-belief contract tests for P11-MEM-08."""

from datetime import datetime, timedelta, timezone

import pytest

from visp_memory.core.beliefs import (
    HYPOTHESIS_TTL_DAYS,
    BeliefType,
    EpistemicStatus,
)
from visp_memory.core.eligibility import filter_recall_eligible
from visp_memory.core.injection import InjectionPolicy, select_for_injection
from visp_memory.core.storage import LocalStorage
from visp_memory.core.trust import filter_unsolicited
from visp_memory.layers.semantic import KnowledgeCategory, SemanticMemory


def _evidence(storage: LocalStorage, content: str = "Observed behavior") -> str:
    return storage.store_evidence(content, repo_id="repo-a")


def test_belief_type_and_epistemic_status_are_exact_closed_vocabularies():
    assert {item.value for item in BeliefType} == {
        "fact",
        "preference",
        "procedure",
        "prohibition",
        "hypothesis",
        "negative",
    }
    assert {item.value for item in EpistemicStatus} == {
        "observed",
        "corroborated",
        "inferred",
        "hypothesized",
        "contradicted",
        "stale",
        "revoked",
    }
    assert {item.value for item in KnowledgeCategory} == {
        item.value for item in BeliefType
    }


@pytest.mark.parametrize(
    ("belief_type", "epistemic_status"),
    [
        *[
            (
                belief_type,
                EpistemicStatus.HYPOTHESIZED
                if belief_type is BeliefType.HYPOTHESIS
                else EpistemicStatus.INFERRED,
            )
            for belief_type in BeliefType
            if belief_type is not BeliefType.PROHIBITION
        ],
        *[(BeliefType.FACT, status) for status in EpistemicStatus],
    ],
)
def test_local_semantic_belief_round_trips_type_and_epistemic_status_independently(
    tmp_path, belief_type, epistemic_status
):
    storage = LocalStorage(tmp_path)
    evidence_id = _evidence(storage)

    memory_id = storage.store_memory(
        "Governed semantic belief",
        layer="semantic",
        repo_id="repo-a",
        category=belief_type.value,
        epistemic_status=epistemic_status.value,
        status="archived",
        evidence_ids=[evidence_id],
        auto_link=False,
    )

    stored = storage.get_memory(memory_id)
    assert stored["belief_type"] == belief_type.value
    assert stored["epistemic_status"] == epistemic_status.value
    assert stored["status"] == "archived"


@pytest.mark.parametrize(
    ("field", "value"),
    [("category", "invariant"), ("epistemic_status", "certain")],
)
def test_local_storage_refuses_unknown_semantic_vocabulary_at_shared_choke_point(
    tmp_path, field, value
):
    storage = LocalStorage(tmp_path)
    kwargs = {
        "category": "invariant",
        "epistemic_status": "observed",
    }
    kwargs[field] = value

    with pytest.raises(ValueError, match="semantic belief"):
        storage.store_memory(
            "Unknown semantic vocabulary",
            layer="semantic",
            repo_id="repo-a",
            evidence_ids=[_evidence(storage)],
            auto_link=False,
            **kwargs,
        )

    assert storage.list_memories(repo_id="repo-a", status="all") == []


def test_episodic_categories_remain_separate_from_semantic_belief_types(tmp_path):
    storage = LocalStorage(tmp_path)

    memory_id = storage.store_memory(
        "Architecture decision recorded",
        layer="episodic",
        repo_id="repo-a",
        category="architecture_decision",
        auto_link=False,
    )

    stored = storage.get_memory(memory_id)
    assert stored["category"] == "architecture_decision"
    assert stored["belief_type"] is None
    assert stored["epistemic_status"] is None


def test_semantic_facade_refuses_unknown_category_before_storage(tmp_path):
    semantic = SemanticMemory(LocalStorage(tmp_path))

    with pytest.raises(ValueError, match="semantic belief type"):
        semantic.establish(
            "Old free-form category",
            category="invariant",
            repo_id="repo-a",
            evidence_ids=[_evidence(semantic.storage)],
        )


@pytest.mark.asyncio
async def test_http_semantic_belief_round_trips_independent_statuses(client):
    headers = {"X-API-KEY": "test_key"}
    evidence = await client.post(
        "/evidence",
        json={"content": "HTTP observation", "repo_id": "repo-a"},
        headers=headers,
    )
    assert evidence.status_code == 200

    response = await client.post(
        "/memories",
        json={
            "content": "HTTP governed belief",
            "layer": "semantic",
            "category": "procedure",
            "status": "archived",
            "repo_id": "repo-a",
            "evidence_ids": [evidence.json()["id"]],
        },
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json()["belief_type"] == "procedure"
    assert response.json()["epistemic_status"] == "inferred"
    assert response.json()["status"] == "archived"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [{"category": "invariant"}, {"category": "invariant", "epistemic_status": "observed"}],
)
async def test_http_refuses_unknown_semantic_vocabulary_without_writing(
    client, payload
):
    headers = {"X-API-KEY": "test_key"}
    evidence = await client.post(
        "/evidence",
        json={"content": "HTTP observation", "repo_id": "repo-a"},
        headers=headers,
    )
    assert evidence.status_code == 200

    response = await client.post(
        "/memories",
        json={
            "content": "Rejected HTTP belief",
            "layer": "semantic",
            "repo_id": "repo-a",
            "evidence_ids": [evidence.json()["id"]],
            **payload,
        },
        headers=headers,
    )

    assert response.status_code == 422
    assert client._transport.app.state.storage.list_memories(
        repo_id="repo-a", status="all"
    ) == []


@pytest.mark.asyncio
async def test_http_forwards_authority_candidate_to_central_verifier(client):
    headers = {"X-API-KEY": "test_key"}
    evidence = await client.post(
        "/evidence",
        json={"content": "Never bypass review", "repo_id": "repo-a"},
        headers=headers,
    )

    response = await client.post(
        "/memories",
        json={
            "content": "Never bypass review",
            "layer": "semantic",
            "category": "prohibition",
            "repo_id": "repo-a",
            "evidence_ids": [evidence.json()["id"]],
            "authority_attestation": "{}",
        },
        headers=headers,
    )

    assert response.status_code == 422
    assert "attestation_version" in response.json()["detail"]


def test_hypothesis_gets_fixed_default_ttl_and_hypothesized_status(tmp_path):
    storage = LocalStorage(tmp_path)
    created_at = "2026-08-02T06:00:00+00:00"

    memory_id = storage.store_memory(
        "A provisional explanation",
        layer="semantic",
        repo_id="repo-a",
        category="hypothesis",
        created_at=created_at,
        evidence_ids=[_evidence(storage)],
        auto_link=False,
    )

    stored = storage.get_memory(memory_id)
    expected_expiry = datetime.fromisoformat(created_at) + timedelta(
        days=HYPOTHESIS_TTL_DAYS
    )
    assert stored["belief_type"] == "hypothesis"
    assert stored["epistemic_status"] == "hypothesized"
    assert datetime.fromisoformat(stored["metadata"]["valid_to"]) == expected_expiry


def test_hypothesis_refuses_ttl_beyond_seven_days_without_writing(tmp_path):
    storage = LocalStorage(tmp_path)
    created_at = datetime(2026, 8, 2, 6, tzinfo=timezone.utc)

    with pytest.raises(ValueError, match="seven-day maximum TTL"):
        storage.store_memory(
            "An overlong provisional explanation",
            layer="semantic",
            repo_id="repo-a",
            category="hypothesis",
            created_at=created_at.isoformat(),
            metadata={"valid_to": (created_at + timedelta(days=8)).isoformat()},
            evidence_ids=[_evidence(storage)],
            auto_link=False,
        )

    assert storage.list_memories(repo_id="repo-a", status="all") == []


@pytest.mark.parametrize(
    ("category", "epistemic_status"),
    [("hypothesis", "inferred"), ("fact", "hypothesized")],
)
def test_provisional_semantic_beliefs_are_excluded_by_shared_canonical_gate(
    category, epistemic_status
):
    candidate = {
        "id": "provisional",
        "content": "Trusted but provisional authentication explanation",
        "layer": "semantic",
        "category": category,
        "belief_type": category,
        "epistemic_status": epistemic_status,
        "repo_id": "repo-a",
        "metadata": {"valid_to": "2026-08-09T06:00:00+00:00"},
        "tags": ["provenance:authored"],
        "source": "authored",
        "created_at": "2026-08-02T06:00:00+00:00",
        "relevance_score": 1.0,
        "importance": 1.0,
    }

    canonical = filter_unsolicited(
        [candidate], now=datetime(2026, 8, 3, tzinfo=timezone.utc)
    )
    injected = select_for_injection(
        [candidate],
        repo_id="repo-a",
        as_of=datetime(2026, 8, 3, tzinfo=timezone.utc),
        task="investigate authentication explanation",
        corpus_size=10,
        policy=InjectionPolicy(min_relevance=0.0),
    )

    assert canonical.allowed == []
    assert canonical.rejected[0].assessment.reason == (
        "provisional semantic belief is explicit-inspection-only"
    )
    assert injected.memories == []


def test_hypothesis_is_explicitly_inspectable_only_before_expiry(tmp_path):
    storage = LocalStorage(tmp_path)
    created_at = datetime(2026, 8, 2, 6, tzinfo=timezone.utc)
    memory_id = storage.store_memory(
        "Scoped provisional diagnosis",
        layer="semantic",
        repo_id="repo-a",
        category="hypothesis",
        created_at=created_at.isoformat(),
        evidence_ids=[_evidence(storage)],
        auto_link=False,
    )
    stored = storage.get_memory(memory_id)

    before = filter_recall_eligible(
        [stored], repo_id="repo-a", as_of=created_at + timedelta(days=6)
    )
    expired = filter_recall_eligible(
        [stored], repo_id="repo-a", as_of=created_at + timedelta(days=7)
    )

    assert before.allowed[0]["epistemic_status"] == "hypothesized"
    assert expired.allowed == []
    assert expired.rejected[0].assessment.code == "expired"
