from visp_memory.core.hybrid_retrieval import HybridRetriever, personalized_pagerank
from visp_memory.core.storage import LocalStorage


def _semantic(storage, content, **kwargs):
    repo_id = kwargs.get("repo_id", "project-a")
    evidence_id = storage.store_evidence(content, repo_id=repo_id)
    return storage.store_memory(content, evidence_ids=[evidence_id], **kwargs)


def _edge(source, target, strength=0.9):
    return {
        "source_id": source,
        "target_id": target,
        "relationship": "supports",
        "strength": strength,
        "evidence": {"confidence": "observed", "confidence_score": strength},
    }


def test_personalized_pagerank_is_degree_normalized_and_deterministic():
    edges = [
        _edge("seed", "specific"),
        _edge("seed", "hub"),
        _edge("hub", "noise-a"),
        _edge("hub", "noise-b"),
        _edge("hub", "noise-c"),
    ]

    first = personalized_pagerank({"seed": 1.0}, edges)
    second = personalized_pagerank({"seed": 1.0}, list(reversed(edges)))

    assert first == second
    assert abs(sum(first.values()) - 1.0) < 1e-6
    assert first["hub"] < first["specific"] * 1.5
    assert first["noise-a"] < first["specific"]


def test_hybrid_retrieval_recovers_multi_hop_evidence_without_hub_override(tmp_path):
    storage = LocalStorage(tmp_path)
    seed = _semantic(storage,
        "OAuth refresh token rotation",
        layer="semantic",
        repo_id="project-a",
        metadata={"confidence": 0.95},
        auto_link=False,
    )
    middle = _semantic(storage,
        "Session invalidation depends on token family lineage",
        layer="semantic",
        repo_id="project-a",
        metadata={"confidence": 0.9},
        auto_link=False,
    )
    evidence = _semantic(storage,
        "Revoke every child session when reuse is detected",
        layer="semantic",
        repo_id="project-a",
        metadata={"confidence": 0.92},
        auto_link=False,
    )
    hub = _semantic(storage,
        "General engineering conventions",
        layer="semantic",
        repo_id="project-a",
        auto_link=False,
    )
    noise_ids = [
        _semantic(storage,
            f"Unrelated convention {index}",
            layer="semantic",
            repo_id="project-a",
            auto_link=False,
        )
        for index in range(4)
    ]
    storage.add_relationship(seed, middle, "supports", evidence={"confidence": "observed"})
    storage.add_relationship(middle, evidence, "supports", evidence={"confidence": "observed"})
    storage.add_relationship(seed, hub, "related_to", evidence={"confidence": "inferred"})
    for noise_id in noise_ids:
        storage.add_relationship(hub, noise_id, "related_to", evidence={"confidence": "inferred"})

    results = HybridRetriever(storage).retrieve(
        "How should OAuth refresh token rotation handle reuse?",
        repo_id="project-a",
        limit=20,
    )
    by_id = {item["id"]: item for item in results}

    assert evidence in by_id
    assert "graph" in by_id[evidence]["retrieval_channels"]
    assert by_id[seed]["relevance_score"] > by_id[hub]["relevance_score"]
    assert by_id[evidence]["retrieval_factors"]["graph_score"] > 0


def test_hybrid_retrieval_uses_exact_code_entities_and_candidate_filter(tmp_path):
    storage = LocalStorage(tmp_path)
    file_decision = _semantic(storage,
        "Use constant-time comparison for configured secrets",
        layer="semantic",
        repo_id="project-a",
        metadata={"files": ["src/auth.py"], "confidence": 0.95},
        auto_link=False,
    )
    hidden = _semantic(storage,
        "Legacy tenant-only authentication behavior",
        layer="semantic",
        repo_id="project-a",
        metadata={"files": ["src/auth.py"], "hidden": True},
        auto_link=False,
    )

    results = HybridRetriever(storage).retrieve(
        "Review credential handling",
        repo_id="project-a",
        files=["src/auth.py"],
        candidate_filter=lambda item: not (item.get("metadata") or {}).get("hidden"),
    )

    assert results[0]["id"] == file_decision
    assert "entity" in results[0]["retrieval_channels"]
    assert hidden not in {item["id"] for item in results}
