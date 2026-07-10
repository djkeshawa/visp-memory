from llm_memory.config import LLMConfig
from llm_memory.core.model_router import ModelRouter
from llm_memory.core.reflection import ReflectionEngine
from llm_memory.core.storage import LocalStorage


def test_reflection_proposals_preserve_evidence_lineage(tmp_path):
    storage = LocalStorage(tmp_path)
    evidence_ids = [
        storage.store_memory(
            f"Deployment observation {index}",
            repo_id="repo-a",
            tags=["deploy"],
            metadata={"confidence": 0.8},
            auto_link=False,
        )
        for index in range(3)
    ]
    engine = ReflectionEngine(storage, ModelRouter(LLMConfig()))
    proposal = next(item for item in engine.preview("repo-a") if item["key"] == "tag:deploy")
    assert proposal["evidence_ids"] == evidence_ids

    reflected = engine.materialize(
        repo_id="repo-a",
        title="Deployment runbook",
        evidence_ids=evidence_ids,
        actor_id="admin",
    )
    memory = storage.get_memory(reflected["id"])
    assert memory["layer"] == "semantic"
    assert memory["category"] == "runbook"
    assert memory["source_ids"] == evidence_ids
    assert memory["metadata"]["lineage"] == evidence_ids
