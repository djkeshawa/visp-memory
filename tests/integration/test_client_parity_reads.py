from visp_memory.quality.conflict import ConflictVerdict

pytest_plugins = ["tests.integration.shared_server"]


def test_two_client_memories_share_the_server_without_crossing_repository_scopes(
    client_memory,
):
    project_a = client_memory("proj-a")
    project_b = client_memory("proj-b")
    memory_a = project_a.record("Project A release note")
    memory_b = project_b.record("Project B release note")

    event_a = project_a.record_utility_feedback(memory_a, "used", query="private A")
    event_b = project_b.record_utility_feedback(memory_b, "dismissed", query="private B")

    report_a = project_a.inspect_utility_signals()
    assert [event["id"] for event in report_a["events"]] == [event_a]
    assert {event["memory_id"] for event in report_a["events"]} == {memory_a}
    assert memory_b not in str(report_a)
    assert project_a.verify_utility_signals() == {
        "valid": True,
        "checked_events": 1,
        "cross_repository_events": 0,
        "violations": [],
    }
    assert project_a.reset_utility_signals() == 1
    assert project_a.inspect_utility_signals()["summary"]["total_events"] == 0
    assert [event["id"] for event in project_b.inspect_utility_signals()["events"]] == [
        event_b
    ]

    assert project_a._storage.peek_memory(memory_a)["id"] == memory_a
    assert project_a._storage.peek_memory(memory_b) is None

    assert project_a._storage.search_turn_keys("release note") == []

    project_a.goal("Ship project A")
    project_b.goal("Ship project B")
    assert project_a._storage.inspect_intent_usage() == {
        "exists": True,
        "memories": 1,
        "active_intents": 1,
        "total_intents": 1,
    }
    assert project_a._storage.inspect_repository_registration() == {
        "exists": True,
        "project_scopes": ["proj-a"],
        "unregistered_scopes": [],
    }

    capabilities = project_a._storage.get_capabilities()
    assert capabilities.audit_log is True
    assert capabilities.reindex is True
    assert capabilities.complete_graph_export is True
    assert capabilities.atomic_graph_import is True
    assert capabilities.vector_search is False


def test_client_supersede_authority_check_uses_remote_peek(client_memory, monkeypatch):
    memory = client_memory("proj-a")
    old_id = memory.learn(
        "Deployments use the blue environment.",
        detect_conflicts=False,
        reconcile=False,
    )
    monkeypatch.setattr(
        memory,
        "check_conflict",
        lambda *args, **kwargs: ConflictVerdict.found(
            {
                "conflict": True,
                "reason": "The deployment environment changed.",
                "conflicting_ids": [old_id],
            }
        ),
    )

    new_id = memory.learn(
        "Deployments use the green environment.",
        detect_conflicts=True,
        reconcile=False,
    )

    old = memory._storage.peek_memory(old_id)
    assert old["status"] == "superseded"
    assert old["metadata"]["superseded_by"] == new_id
