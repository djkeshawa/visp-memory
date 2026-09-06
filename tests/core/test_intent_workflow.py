import pytest
from pydantic import ValidationError

from visp_memory.core.intent_workflow import IntentWorkflowReport
from visp_memory.core.storage import LocalStorage


def report(**changes):
    return {
        "source": "assistant",
        "task_id": "task-1",
        "event_id": "event-1",
        "revision": 1,
        "status": "completed",
        "summary": "Login delivered",
        "evidence": [{"description": "Authentication acceptance tests passed"}],
        **changes,
    }


def test_external_completion_is_visible_and_duplicate_report_is_idempotent(tmp_path):
    storage = LocalStorage(tmp_path)
    intent = storage.set_intent("Deliver login", repo_id="sample")
    result = storage.report_intent_workflow(intent, report(), actor_id="host", channel="cli")
    assert result["status"] == "completed"
    assert storage.get_active_intents(repo_id="sample") == []
    duplicate = storage.report_intent_workflow(intent, report(), actor_id="host", channel="cli")
    assert not duplicate["applied"]
    saved = storage.get_active_intents(repo_id="sample", status="completed")[0]
    assert len(saved["context"]["workflow_history"]) == 1
    storage.report_intent_workflow(
        intent,
        report(status="active", revision=2, event_id="reopened"),
        actor_id="host",
        channel="cli",
    )
    assert storage.get_active_intents(repo_id="sample")[0]["id"] == intent


@pytest.mark.parametrize(
    "change",
    [
        {"revision": 1, "status": "closed"},
        {"source": "other"},
        {"task_id": "other"},
        {"revision": 2},
    ],
)
def test_conflicting_stale_and_reused_events_are_refused(tmp_path, change):
    storage = LocalStorage(tmp_path)
    intent = storage.set_intent("Deliver login", repo_id="sample")
    storage.report_intent_workflow(intent, report(), actor_id="host", channel="cli")
    with pytest.raises(ValueError):
        storage.report_intent_workflow(intent, report(**change), actor_id="host", channel="cli")


def test_other_actor_and_context_edit_cannot_replace_workflow_report(tmp_path):
    storage = LocalStorage(tmp_path)
    intent = storage.set_intent("Deliver login", repo_id="sample")
    storage.report_intent_workflow(intent, report(), actor_id="host", channel="cli")
    with pytest.raises(ValueError):
        storage.report_intent_workflow(intent, report(), actor_id="other", channel="cli")
    storage.update_intent(intent, context={"external_workflow": {"source": "fake"}})
    saved = storage.get_active_intents(status="all")[0]
    assert saved["context"]["external_workflow"]["source"] == "assistant"


@pytest.mark.parametrize(
    "changes",
    [
        {"evidence": []},
        {"checks": [{"description": "Tests", "status": "failed"}]},
        {"checks": [{"description": "Review", "status": "pending"}]},
    ],
)
def test_completion_report_requires_consistent_support(changes):
    with pytest.raises(ValidationError):
        IntentWorkflowReport.model_validate(report(**changes))


def test_generic_context_cannot_forge_a_report(tmp_path):
    store = LocalStorage(tmp_path)
    intent = store.set_intent(
        "Task",
        context={
            "external_workflow": {"source": "fake"},
            "workflow_history": [{"status": "completed"}],
        },
    )
    assert store.get_active_intents()[0]["context"] == {}
    store.report_intent_workflow(intent, report(), actor_id="host", channel="cli")
    store.update_intent(intent, context={"completion_evaluation": {"decision": "suggested"}})
    assert "completion_evaluation" not in store.get_active_intents(status="all")[0]["context"]


@pytest.mark.parametrize(
    "changes",
    [
        {"revision": True},
        {"evidence": [{"description": "Result", "url": "https://user:password@example.com/run"}]},
        {"checks": [{"description": "   ", "status": "passed"}]},
    ],
)
def test_report_refuses_invalid_or_credential_bearing_input(changes):
    with pytest.raises(ValidationError):
        IntentWorkflowReport.model_validate(report(**changes))
