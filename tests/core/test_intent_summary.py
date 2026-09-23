"""Regression coverage for the intent summary read path."""

from visp_memory.layers.intent import IntentMemory


def test_summarize_reuses_one_active_intent_read():
    class CountingStorage:
        def __init__(self):
            self.calls = 0

        def get_active_intents(self, *, repo_id):
            self.calls += 1
            return [
                {
                    "id": "focus-1",
                    "description": "FOCUS: ship safely",
                    "context": {"constraints": ["No scope creep"]},
                },
                {
                    "id": "task-1",
                    "description": "WORKING ON: the summary",
                    "context": {},
                },
            ]

    storage = CountingStorage()

    summary = IntentMemory(storage).summarize(repo_id="repo-a")

    assert storage.calls == 1
    assert summary["focus"]["id"] == "focus-1"
    assert summary["constraints"] == ["No scope creep"]
    assert summary["current_task"]["id"] == "task-1"
    assert [item["id"] for item in summary["all_goals"]] == ["focus-1", "task-1"]
