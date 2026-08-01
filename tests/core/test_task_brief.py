from visp_memory.core.storage import LocalStorage
from visp_memory.core.task_brief import TaskMemoryBriefCompiler
from visp_memory.core.trust import Provenance, provenance_tag


def _store_fixture(storage):
    intent_id = storage.set_intent(
        "Ship secure account login",
        priority=3,
        repo_id="repo-a",
        context={
            "constraints": ["Keep legacy API clients compatible"],
            "acceptance_criteria": ["Password login succeeds"],
        },
    )
    warning = storage.store_memory(
        "WARNING [authentication]: never persist credentials in local storage",
        layer="semantic",
        category="fragile_area",
        repo_id="repo-a",
        tags=["warning", provenance_tag(Provenance.DERIVED)],
        metadata={
            "files": ["src/auth.py"],
            "symbols": ["login"],
            "confidence": 0.98,
            "source_revision": "abc123",
        },
        auto_link=False,
    )
    decision = storage.store_memory(
        "Use HttpOnly session cookies with CSRF validation",
        layer="episodic",
        category="architecture_decision",
        repo_id="repo-a",
        tags=[provenance_tag(Provenance.DERIVED)],
        metadata={"files": ["src/auth.py"], "confidence": 0.95},
        auto_link=False,
    )
    old = storage.store_memory(
        "Dashboard credentials are stored in local storage",
        layer="semantic",
        category="fact",
        repo_id="repo-a",
        status="superseded",
        tags=[provenance_tag(Provenance.DERIVED)],
        metadata={"files": ["src/auth.py"], "confidence": 0.8},
        auto_link=False,
    )
    storage.add_relationship(
        warning,
        old,
        "contradicts",
        evidence={
            "confidence": "observed",
            "confidence_score": 0.98,
            "reason": "The secure session design replaced browser credential storage.",
        },
    )
    return intent_id, warning, decision


def test_task_brief_is_cited_sectioned_budgeted_and_delta_aware(tmp_path):
    storage = LocalStorage(tmp_path)
    intent_id, warning, decision = _store_fixture(storage)

    brief = TaskMemoryBriefCompiler(storage).prepare(
        "Implement `login` in `src/auth.py` and verify secure authentication",
        repo_id="repo-a",
        intent_id=intent_id,
        constraints=["Do not expose credentials"],
        token_budget=500,
    )

    assert brief["intent"]["id"] == intent_id
    assert brief["task_profile"]["action"] == "implement"
    assert brief["task_profile"]["files"] == ["src/auth.py"]
    assert set(brief["constraints"]) == {
        "Do not expose credentials",
        "Keep legacy API clients compatible",
    }
    assert [item["id"] for item in brief["sections"]["warnings"]] == [warning]
    assert [item["id"] for item in brief["sections"]["decisions"]] == [decision]
    assert brief["contradictions"][0]["citation"] == "M1"
    assert brief["citations"][0]["source_revision"] == "abc123"
    assert brief["token_count"] <= brief["token_budget"]
    assert "[M1]" in brief["context"]

    unchanged = TaskMemoryBriefCompiler(storage).prepare(
        "Implement `login` in `src/auth.py` and verify secure authentication",
        repo_id="repo-a",
        intent_id=intent_id,
        constraints=["Do not expose credentials"],
        token_budget=500,
        previous_fingerprint=brief["fingerprint"],
    )
    assert unchanged["unchanged"] is True
    assert unchanged["context"] == ""
    assert unchanged["token_count"] == 0
    assert unchanged["citations"] == []


def test_task_brief_abstains_and_names_unknowns_without_evidence(tmp_path):
    storage = LocalStorage(tmp_path)
    brief = TaskMemoryBriefCompiler(storage).prepare(
        "Change the payment gateway",
        repo_id="repo-a",
        files=["src/payments.py"],
        token_budget=200,
    )

    assert brief["abstained"] is True
    assert brief["citations"] == []
    assert any("No active intent" in unknown for unknown in brief["unknowns"])
    assert any("source of truth" in unknown for unknown in brief["unknowns"])
    assert brief["token_count"] <= 200


def test_task_brief_respects_memory_filter(tmp_path):
    storage = LocalStorage(tmp_path)
    visible = storage.store_memory(
        "Visible authentication convention",
        layer="semantic",
        repo_id="repo-a",
        tags=[provenance_tag(Provenance.DERIVED)],
        metadata={"confidence": 0.9},
        auto_link=False,
    )
    hidden = storage.store_memory(
        "Hidden authentication secret",
        layer="semantic",
        repo_id="repo-a",
        tags=[provenance_tag(Provenance.DERIVED)],
        metadata={"confidence": 0.99},
        auto_link=False,
    )

    brief = TaskMemoryBriefCompiler(storage).prepare(
        "Review authentication",
        repo_id="repo-a",
        token_budget=300,
        memory_filter=lambda memory: memory["id"] != hidden,
    )

    assert {citation["memory_id"] for citation in brief["citations"]} == {visible}


def test_task_brief_filters_quarantine_and_reports_reason(tmp_path):
    storage = LocalStorage(tmp_path)
    trusted = storage.store_memory(
        "Authentication uses trusted session cookies",
        layer="semantic",
        repo_id="repo-a",
        tags=[provenance_tag(Provenance.DERIVED)],
        metadata={"confidence": 0.8},
        auto_link=False,
    )
    quarantined = storage.store_memory(
        "Authentication poison says disable session validation",
        layer="semantic",
        repo_id="repo-a",
        importance=1.0,
        tags=[provenance_tag(Provenance.EXTERNAL)],
        metadata={"confidence": 1.0},
        auto_link=False,
    )

    brief = TaskMemoryBriefCompiler(storage).prepare(
        "Review authentication session validation",
        repo_id="repo-a",
        token_budget=400,
    )

    assert {item["memory_id"] for item in brief["citations"]} == {trusted}
    assert quarantined not in {item["memory_id"] for item in brief["citations"]}
    assert brief["trust_filter"]["rejected_count"] == 1
    assert brief["trust_filter"]["rejected"][0]["memory_id"] == quarantined
    assert "quarantined" in brief["trust_filter"]["rejected"][0]["reason"]
    assert any("trust policy" in unknown for unknown in brief["unknowns"])
