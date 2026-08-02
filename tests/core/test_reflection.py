from visp_memory.config import LLMConfig
from visp_memory.core.model_router import ModelRouter
from visp_memory.core.reflection import ReflectionEngine
from visp_memory.core.storage import LocalStorage
from visp_memory.core.trust import Provenance, provenance_of


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
    assert memory["category"] == "procedure"
    assert memory["source_ids"] == evidence_ids
    assert memory["metadata"]["lineage"] == evidence_ids
    assert memory["metadata"]["write_channel"] == "reflection"
    assert memory["metadata"]["legacy_category"] == "runbook"
    assert provenance_of(memory) is Provenance.ASSISTED
