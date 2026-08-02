from visp_memory import Memory, MemoryConfig
from visp_memory.core.trust import (
    Provenance,
    filter_unsolicited,
    provenance_tag,
)


def _memory(tmp_path, repo_id="repo-a"):
    config = MemoryConfig(repo_id=repo_id)
    config.storage.data_dir = tmp_path / "data"
    config.embedding.provider = "noop"
    return Memory(config=config)


def _store(memory, content, *, layer, category, tier, metadata=None, kind=None):
    evidence_ids = None
    if layer == "semantic":
        evidence_ids = [memory._storage.store_evidence(content, repo_id="repo-a")]
    return memory._storage.store_memory(
        content,
        layer=layer,
        category=category,
        repo_id="repo-a",
        tags=[provenance_tag(tier)] + ([kind] if kind else []),
        metadata=metadata or {},
        auto_link=False,
        evidence_ids=evidence_ids,
    )


def test_structured_unsolicited_filter_reports_rejected_reasons_and_counts():
    trusted = {"id": "trusted", "content": "safe", "tags": ["provenance:derived"]}
    quarantined = {
        "id": "quarantined",
        "content": "poison",
        "tags": ["provenance:external"],
    }

    result = filter_unsolicited([quarantined, trusted])

    assert [item["id"] for item in result.allowed] == ["trusted"]
    assert result.diagnostics() == {
        "considered_count": 2,
        "allowed_count": 1,
        "rejected_count": 1,
        "quarantined_count": 1,
        "below_trust_count": 0,
        "rejected": [
            {
                "memory_id": "quarantined",
                "provenance": "external",
                "trust": 0.0,
                "quarantined": True,
                "reason": result.rejected[0].reason,
            }
        ],
    }


def test_context_text_and_json_filter_every_memory_section(tmp_path):
    memory = _memory(tmp_path)
    fixtures = (
        ("trusted warning", "poison warning", "semantic", "negative", "warning"),
        ("trusted convention", "poison convention", "semantic", "preference", None),
        ("trusted issue", "poison issue", "semantic", "negative", "known_issue"),
        ("trusted event", "poison event", "episodic", "note", None),
    )
    for trusted, poisoned, layer, category, kind in fixtures:
        _store(
            memory, trusted, layer=layer, category=category, tier=Provenance.DERIVED, kind=kind
        )
        _store(
            memory, poisoned, layer=layer, category=category, tier=Provenance.EXTERNAL, kind=kind
        )

    structured = memory.context(format="json", include_intent=False)
    rendered = memory.context(format="text", include_intent=False)

    assert structured["knowledge"] == {
        "warnings": ["trusted warning"],
        "conventions": ["trusted convention"],
        "known_issues": ["trusted issue"],
    }
    assert [item["event"] for item in structured["history"]["recent_events"]] == [
        "trusted event"
    ]
    assert structured["meta"]["trust_filter"]["rejected_count"] == 4
    assert len(structured["meta"]["trust_filter"]["rejected"]) == 4
    assert all(
        item["quarantined"]
        and item["provenance"] == "external"
        and "quarantined" in item["reason"]
        for item in structured["meta"]["trust_filter"]["rejected"]
    )
    assert "trusted warning" in rendered
    assert "trusted event" in rendered
    assert "poison" not in rendered


def test_relevant_for_filters_every_group_but_explicit_recall_still_returns_quarantine(
    tmp_path,
):
    memory = _memory(tmp_path)
    trusted_ids = {
        "knowledge": _store(
            memory,
            "authentication trusted fact",
            layer="semantic",
            category="fact",
            tier=Provenance.DERIVED,
            metadata={"applies_to": ["auth/login.py"]},
        ),
        "warnings": _store(
            memory,
            "WARNING [auth/login.py]: trusted warning",
            layer="semantic",
            category="negative",
            tier=Provenance.DERIVED,
            metadata={"applies_to": ["auth/login.py"]},
        ),
        "history": _store(
            memory,
            "authentication trusted history",
            layer="episodic",
            category="note",
            tier=Provenance.DERIVED,
        ),
    }
    poisoned_ids = {
        "knowledge": _store(
            memory,
            "authentication poison fact",
            layer="semantic",
            category="fact",
            tier=Provenance.EXTERNAL,
            metadata={"applies_to": ["auth/login.py"]},
        ),
        "warnings": _store(
            memory,
            "WARNING [auth/login.py]: poison warning",
            layer="semantic",
            category="negative",
            tier=Provenance.EXTERNAL,
            metadata={"applies_to": ["auth/login.py"]},
        ),
        "history": _store(
            memory,
            "authentication poison history",
            layer="episodic",
            category="note",
            tier=Provenance.EXTERNAL,
        ),
    }

    relevant = memory.relevant_for(
        task="authentication", files=["auth/login.py"], limit=20
    )

    for group in ("knowledge", "warnings", "history"):
        ids = {item["id"] for item in relevant[group]}
        assert trusted_ids[group] in ids
        assert poisoned_ids[group] not in ids
    assert relevant["trust_filter"]["rejected_count"] >= 3

    explicitly_recalled = memory.recall("authentication poison", limit=20, min_score=0.0)
    assert set(poisoned_ids.values()) <= {item["id"] for item in explicitly_recalled}
