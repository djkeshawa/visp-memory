import pytest

from visp_memory import Memory, MemoryConfig
from visp_memory.core.context_compiler import ContextCompiler
from visp_memory.core.eligibility import filter_recall_eligible
from visp_memory.core.injection import (
    InjectionPolicy,
    build_session_brief,
    inject_for_task,
    select_for_injection,
)
from visp_memory.core.storage import LocalStorage
from visp_memory.core.task_brief import TaskMemoryBriefCompiler
from visp_memory.core.trust import Provenance, provenance_tag

AS_OF = "2026-01-15T12:00:00+00:00"


def _memory(tmp_path, repo_id="repo-a"):
    config = MemoryConfig(repo_id=repo_id)
    config.storage.data_dir = tmp_path / "memory"
    config.embedding.provider = "noop"
    return Memory(config=config)


def _store(
    storage,
    content,
    *,
    repo_id="repo-a",
    metadata=None,
    tier=Provenance.EXTERNAL,
    category="fact",
):
    effective_repo_id = repo_id or "__visp_unscoped__"
    evidence_id = storage.store_evidence(content, repo_id=effective_repo_id)
    return storage.store_memory(
        content,
        layer="semantic",
        category=category,
        repo_id=repo_id,
        evidence_ids=[evidence_id],
        tags=[provenance_tag(tier)],
        metadata=metadata or {},
        auto_link=False,
    )


def test_primary_recall_enforces_time_repo_environment_and_task_scope(tmp_path):
    memory = _memory(tmp_path)
    storage = memory._storage
    visible_quarantined = _store(
        storage,
        "scope recall current quarantined",
        metadata={
            "valid_from": AS_OF,
            "environment": ["prod", "staging"],
            "task_type": "deploy",
        },
    )
    universal = _store(storage, "scope recall repository universal")
    excluded = {
        _store(
            storage,
            "scope recall expired",
            metadata={"valid_to": AS_OF},
        ),
        _store(
            storage,
            "scope recall not yet valid",
            metadata={"valid_from": "2026-01-15T12:00:01+00:00"},
        ),
        _store(
            storage,
            "scope recall wrong environment",
            metadata={"environment": "dev"},
        ),
        _store(
            storage,
            "scope recall wrong task",
            metadata={"task_type": ["review", "test"]},
        ),
        _store(
            storage,
            "scope recall malformed environment",
            metadata={"environment": {"name": "prod"}},
        ),
        _store(
            storage,
            "scope recall malformed date",
            metadata={"valid_to": "not-a-date"},
        ),
        _store(storage, "scope recall other repository", repo_id="repo-b"),
    }

    results = memory.recall(
        "scope recall",
        repo_id="repo-a",
        environment="prod",
        task_type="deploy",
        as_of=AS_OF,
        min_score=0.0,
        limit=30,
    )

    result_ids = {item["id"] for item in results}
    assert result_ids == {visible_quarantined, universal}
    assert result_ids.isdisjoint(excluded)


def test_primary_recall_refuses_absent_repository_scope(tmp_path):
    memory = _memory(tmp_path, repo_id=None)
    _store(memory._storage, "must not become a global recall", repo_id="repo-a")
    _store(memory._storage, "must not mix a second repo", repo_id="repo-b")

    with pytest.raises(ValueError, match="repo_id.*required"):
        memory.recall("must not", min_score=0.0)


def test_primary_recall_refuses_malformed_caller_scope_even_for_unscoped_records(tmp_path):
    memory = _memory(tmp_path)
    _store(memory._storage, "unscoped record must not bypass caller validation")

    with pytest.raises(ValueError, match="malformed environment scope"):
        memory.recall(
            "caller validation",
            repo_id="repo-a",
            environment={"name": "prod"},
            min_score=0.0,
        )


@pytest.mark.parametrize("metadata", [[], ""])
def test_eligibility_rejects_falsy_non_mapping_metadata(metadata):
    result = filter_recall_eligible(
        [{"id": "malformed", "repo_id": "repo-a", "metadata": metadata}],
        repo_id="repo-a",
        as_of=AS_OF,
    )

    assert result.allowed == []
    assert result.rejected[0].assessment.code == "malformed_metadata"


def test_injection_rejects_expired_and_malformed_validity_with_structured_reasons():
    base = {
        "layer": "semantic",
        "category": "fact",
        "repo_id": "repo-a",
        "tags": [provenance_tag(Provenance.DERIVED)],
    }
    candidates = [
        {
            **base,
            "id": "current",
            "content": "deploy authentication session changes safely",
            "relevance_score": 0.95,
            "metadata": {"environment": "prod", "task_type": "deploy"},
        },
        {
            **base,
            "id": "expired",
            "content": "expired deploy authentication session changes",
            "relevance_score": 0.99,
            "metadata": {"valid_to": AS_OF},
        },
        {
            **base,
            "id": "malformed",
            "content": "malformed deploy authentication session changes",
            "relevance_score": 0.98,
            "metadata": {"valid_from": "yesterday-ish"},
        },
    ]

    result = select_for_injection(
        candidates,
        task="deploy authentication session changes",
        repo_id="repo-a",
        environment="prod",
        task_type="deploy",
        as_of=AS_OF,
        corpus_size=50,
        policy=InjectionPolicy(min_margin=0.0),
    )

    assert [item["id"] for item in result.memories] == ["current"]
    diagnostics = result.as_dict()["eligibility_filter"]
    assert diagnostics["rejected_count"] == 2
    reasons = {item["reason"] for item in diagnostics["rejected"]}
    assert any("expired" in reason for reason in reasons)
    assert any("malformed valid_from" in reason for reason in reasons)


def test_inject_for_task_threads_scope_into_candidate_recall(tmp_path):
    memory = _memory(tmp_path)
    matched = _store(
        memory._storage,
        "WARNING [auth.py]: production deploy authentication sessions carefully",
        tier=Provenance.DERIVED,
        category="fragile_area",
        metadata={
            "environment": "prod",
            "task_type": "deploy",
            "applies_to": ["auth.py"],
        },
    )

    result = inject_for_task(
        memory,
        task="deploy authentication sessions carefully",
        files=["auth.py"],
        repo_id="repo-a",
        environment="prod",
        task_type="deploy",
        as_of=AS_OF,
        policy=InjectionPolicy(min_corpus=1, min_task_terms=0, min_margin=0.0),
        repo_root=tmp_path,
    )

    assert matched in {item["id"] for item in result.memories}


def test_context_compiler_normalizes_and_enforces_declared_environment_and_task_scope(tmp_path):
    storage = LocalStorage(tmp_path / "context")
    universal = _store(storage, "context scope universal", tier=Provenance.DERIVED)
    matched = _store(
        storage,
        "context scope matched",
        tier=Provenance.DERIVED,
        metadata={"environment": ["staging", "prod", "prod"], "task_type": "deploy"},
    )
    wrong_environment = _store(
        storage,
        "context scope wrong environment",
        tier=Provenance.DERIVED,
        metadata={"environment": "dev"},
    )
    wrong_task = _store(
        storage,
        "context scope wrong task",
        tier=Provenance.DERIVED,
        metadata={"task_type": ["review", "test"]},
    )
    malformed = _store(
        storage,
        "context scope malformed",
        tier=Provenance.DERIVED,
        metadata={"task_type": ["deploy", 7]},
    )

    result = ContextCompiler(storage).compile(
        "context scope",
        repo_id="repo-a",
        environment="prod",
        task_type="deploy",
        as_of=AS_OF,
        token_budget=2000,
    )
    result_ids = {item["id"] for item in result["items"]}
    assert {universal, matched} <= result_ids
    assert result_ids.isdisjoint({wrong_environment, wrong_task, malformed})

    omitted_caller_scope = ContextCompiler(storage).compile(
        "context scope matched",
        repo_id="repo-a",
        as_of=AS_OF,
        token_budget=2000,
    )
    assert matched not in {item["id"] for item in omitted_caller_scope["items"]}


def test_auto_hook_session_brief_filters_quarantine_and_requires_repo_scope(tmp_path):
    memory = _memory(tmp_path)
    _store(
        memory._storage,
        "WARNING [auth.py]: trusted session warning",
        metadata={"applies_to": ["auth.py"]},
        tier=Provenance.DERIVED,
        category="fragile_area",
    )
    _store(
        memory._storage,
        "WARNING [auth.py]: poison session warning",
        metadata={"applies_to": ["auth.py"]},
        tier=Provenance.EXTERNAL,
        category="fragile_area",
    )

    brief = build_session_brief(memory)

    assert "trusted session warning" in brief
    assert "poison session warning" not in brief

    unscoped = _memory(tmp_path / "unscoped", repo_id=None)
    with pytest.raises(ValueError, match="repo_id.*required"):
        build_session_brief(unscoped)


def test_task_brief_forwards_environment_and_task_scope(tmp_path):
    storage = LocalStorage(tmp_path / "brief")
    matched = _store(
        storage,
        "authentication deployment matched evidence",
        tier=Provenance.DERIVED,
        metadata={"environment": "prod", "task_type": "deploy", "confidence": 0.9},
    )
    excluded = _store(
        storage,
        "authentication deployment wrong environment",
        tier=Provenance.DERIVED,
        metadata={"environment": "dev", "task_type": "deploy", "confidence": 0.9},
    )

    brief = TaskMemoryBriefCompiler(storage).prepare(
        "deploy authentication changes",
        repo_id="repo-a",
        environment="prod",
        task_type="deploy",
        as_of=AS_OF,
        token_budget=2000,
    )

    citation_ids = {item["memory_id"] for item in brief["citations"]}
    assert matched in citation_ids
    assert excluded not in citation_ids


def test_public_compilers_and_proactive_recall_canonicalize_runtime_scope(tmp_path):
    memory = _memory(tmp_path)
    storage = memory._storage
    _store(
        storage,
        "canonical production deployment evidence",
        tier=Provenance.DERIVED,
        metadata={
            "environment": ["prod", "staging"],
            "task_type": "deploy",
            "confidence": 0.9,
        },
    )
    compiler = ContextCompiler(storage)
    first = compiler.compile(
        "canonical production deployment evidence",
        repo_id="repo-a",
        environment=["STAGING", "prod", "PROD"],
        task_type=["DEPLOY", "deploy"],
        as_of=AS_OF,
    )
    second = compiler.compile(
        "canonical production deployment evidence",
        repo_id="repo-a",
        environment=["prod", "staging"],
        task_type="deploy",
        as_of=AS_OF,
    )
    brief = TaskMemoryBriefCompiler(storage).prepare(
        "canonical production deployment evidence",
        repo_id="repo-a",
        environment=["STAGING", "prod", "PROD"],
        task_type=["DEPLOY", "deploy"],
        as_of=AS_OF,
    )

    from visp_memory.recall.proactive import ProactiveRecall

    proactive = ProactiveRecall(
        memory,
        environment=["STAGING", "prod", "PROD"],
        task_type=["DEPLOY", "deploy"],
        as_of=AS_OF,
    )

    assert first["fingerprint"] == second["fingerprint"]
    assert first["environment"] == ["prod", "staging"]
    assert first["task_type"] == ["deploy"]
    assert brief["environment"] == ["prod", "staging"]
    assert brief["task_type"] == ["deploy"]
    assert proactive.environment == ["prod", "staging"]
    assert proactive.task_type == ["deploy"]
