from unittest import mock

from visp_memory.config import LLMConfig
from visp_memory.core.intent_evaluator import IntentEvaluator
from visp_memory.core.model_router import ModelRouter
from visp_memory.core.storage import LocalStorage


def test_completion_language_is_advisory_and_does_not_mutate_intent(tmp_path):
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

    assert result["decision"] == "suggested"
    assert result["completion_signal"] is True
    assert result["objective_evidence"] is False
    assert result["authoritative"] is False
    assert result["status_changed"] is False
    assert result["evaluator_version"] == "intent-advisory-v2"
    assert result["deprecated_inputs"] == {
        "allow_auto_complete": "accepted_but_ineffective",
        "intent_auto_complete": "accepted_but_ineffective",
    }
    active = storage.get_active_intents(repo_id="repo-a", status="active")
    assert active[0]["id"] == intent_id
    assert active[0]["context"] == {}
    assert storage.get_active_intents(repo_id="repo-a", status="completed") == []


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
    assert result["authoritative"] is False
    assert result["status_changed"] is False
    assert storage.get_active_intents(repo_id="repo-a")[0]["status"] == "active"


def test_deprecated_auto_complete_inputs_are_accepted_but_ineffective(tmp_path):
    storage = LocalStorage(tmp_path)
    intent_id = storage.set_intent("Implement secure login", repo_id="repo-a")
    evidence_id = storage.store_memory(
        "Implemented secure login and all tests passed",
        repo_id="repo-a",
        auto_link=False,
    )
    config = LLMConfig(intent_auto_complete=True)
    evaluator = IntentEvaluator(storage, ModelRouter(config), config)

    result = evaluator.evaluate(
        storage.get_active_intents(repo_id="repo-a")[0],
        summary="Implemented secure login and all tests passed",
        memory_ids=[evidence_id],
        actor_id="worker",
        allow_auto_complete=True,
    )

    assert result["decision"] == "suggested"
    assert result["status_changed"] is False
    assert storage.get_active_intents(repo_id="repo-a")[0]["id"] == intent_id


def test_evaluation_never_invokes_configured_model_router(tmp_path):
    storage = LocalStorage(tmp_path)
    storage.set_intent("Implement secure login", repo_id="repo-a")
    evidence_id = storage.store_memory(
        "Implemented secure login and all tests passed",
        repo_id="repo-a",
        auto_link=False,
    )
    config = LLMConfig(provider="ollama", model="configured-but-unused")
    router = ModelRouter(config)
    evaluator = IntentEvaluator(storage, router, config)

    with mock.patch.object(
        router,
        "complete_json",
        side_effect=AssertionError("intent evaluation must remain deterministic"),
    ) as completion:
        result = evaluator.evaluate(
            storage.get_active_intents(repo_id="repo-a")[0],
            summary="Implemented secure login and all tests passed",
            memory_ids=[evidence_id],
            actor_id="worker",
        )

    completion.assert_not_called()
    assert result["model_confidence"] is None
    assert result["provider"] is None
    assert result["model"] is None


def test_model_router_prefers_sampling_callback():
    router = ModelRouter(LLMConfig(provider="none"))
    result = router.complete(
        "reflection",
        "Find a reusable principle",
        sampling_callback=lambda prompt, system: f"sampled: {prompt}",
    )
    assert result["provider"] == "mcp-sampling"
    assert result["text"].startswith("sampled:")


def test_negative_and_unrelated_text_cannot_suggest_completion(tmp_path):
    storage = LocalStorage(tmp_path)
    storage.set_intent("Implement secure login", repo_id="repo-a")
    config = LLMConfig()
    evaluator = IntentEvaluator(storage, ModelRouter(config), config)
    for text in ["Secure login is not completed; test failed.",
                 "Merged documentation changes. Test passed."]:
        memory_id = storage.store_memory(text, repo_id="repo-a", auto_link=False)
        result = evaluator.evaluate(storage.get_active_intents(repo_id="repo-a")[0],
            summary=text, memory_ids=[memory_id], actor_id="worker")
        assert result["decision"] == "incomplete"
        assert result["reason"]
