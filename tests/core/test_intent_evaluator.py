from llm_memory.config import LLMConfig
from llm_memory.core.intent_evaluator import IntentEvaluator
from llm_memory.core.model_router import ModelRouter
from llm_memory.core.storage import LocalStorage


def test_objective_evidence_auto_completes_intent(tmp_path):
    storage = LocalStorage(tmp_path)
    intent_id = storage.set_intent("Implement secure login", repo_id="repo-a")
    evidence_id = storage.store_memory(
        "Implemented secure login and all tests passed",
        repo_id="repo-a",
        auto_link=False,
    )
    config = LLMConfig()
    evaluator = IntentEvaluator(storage, ModelRouter(config), config)

    result = evaluator.evaluate(
        storage.get_active_intents(repo_id="repo-a")[0],
        summary="Implemented secure login and all tests passed",
        memory_ids=[evidence_id],
        actor_id="worker",
    )

    assert result["decision"] == "completed"
    assert result["objective_evidence"] is True
    completed = storage.get_active_intents(repo_id="repo-a", status="completed")
    assert completed[0]["id"] == intent_id
    assert completed[0]["context"]["completed_automatically"] is True


def test_completion_language_without_objective_memory_cannot_auto_complete(tmp_path):
    storage = LocalStorage(tmp_path)
    storage.set_intent("Implement secure login", repo_id="repo-a")
    config = LLMConfig()
    evaluator = IntentEvaluator(storage, ModelRouter(config), config)

    result = evaluator.evaluate(
        storage.get_active_intents(repo_id="repo-a")[0],
        summary="Implemented secure login and tests passed",
        memory_ids=[],
        actor_id="worker",
    )

    assert result["decision"] != "completed"
    assert storage.get_active_intents(repo_id="repo-a")[0]["status"] == "active"


def test_model_router_prefers_sampling_callback():
    router = ModelRouter(LLMConfig(provider="none"))
    result = router.complete(
        "reflection",
        "Find a reusable principle",
        sampling_callback=lambda prompt, system: f"sampled: {prompt}",
    )
    assert result["provider"] == "mcp-sampling"
    assert result["text"].startswith("sampled:")
