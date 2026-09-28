import json

import requests

from visp_memory.core.attribution import WriterIdentity, bind_writer
from visp_memory.core.indexing import ReindexScope
from visp_memory.core.remote.owner_auth import owner_token_path_for_request
from visp_memory.quality.conflict import ConflictVerdict

pytest_plugins = ["tests.integration.shared_server"]


def test_shared_server_projects_workflows_and_owner_maintenance(
    client_memory, shared_server, tmp_path, monkeypatch
):
    a, b = client_memory("proj-a"), client_memory("proj-b")
    with bind_writer(WriterIdentity(agent="claude-code", session="s1")):
        a_id = a._storage.store_memory(
            "Project A private deployment phrase",
            repo_id="proj-a",
            tags=["provenance:assisted", "release"],
            metadata={"written_by": {"agent": "forged"}},
        )
        a_intent = a.goal("Ship project A release")
        evidence_id = a._storage.store_evidence(
            "A evidence",
            "proj-a",
            provenance="assisted",
            metadata={"written_by": {"agent": "forged"}},
        )
        evidence_plain_id = a._storage.store_evidence(
            "Evidence without attribution", "proj-a", provenance="assisted"
        )
    with bind_writer(WriterIdentity(agent="codex", session="s2")):
        b_id = b._storage.store_memory("Project B private migration phrase", repo_id="proj-b")
        b_intent = b.goal("Ship project B release")
    plain_id = a._storage.store_memory(
        "Same semantics without attribution",
        repo_id="proj-a",
        tags=["provenance:assisted", "release"],
    )

    for memory, own_phrase, foreign_phrase, foreign_id in (
        (a, "Project A private", "Project B private", b_id),
        (b, "Project B private", "Project A private", a_id),
    ):
        assert own_phrase in str(memory.recall(own_phrase, min_score=0))
        assert foreign_phrase not in str(memory.recall(own_phrase, min_score=0))
        assert foreign_phrase not in str(memory.context(format="json"))
        brief = requests.post(
            f"{shared_server}/context/brief",
            json={"task": own_phrase, "repo_id": memory.config.repo_id},
            timeout=5,
        )
        assert brief.status_code == 200, brief.text
        assert foreign_phrase not in brief.text
        assert foreign_id not in {
            row["id"] for row in memory._storage.list_memories(repo_id=memory.config.repo_id)
        }
    assert requests.get(f"{shared_server}/memories", timeout=5).status_code in {400, 422}
    assert requests.post(
        f"{shared_server}/recall",
        json={"query": "private"},
        timeout=5,
    ).status_code in {400, 422}

    stored = a._storage.peek_memory(a_id)
    assert stored["metadata"]["written_by"] == {"agent": "claude-code", "session": "s1"}
    evidence = a._storage.list_evidence(repo_id="proj-a")
    attributed_evidence = next(row for row in evidence if row["id"] == evidence_id)
    plain_evidence = next(row for row in evidence if row["id"] == evidence_plain_id)
    assert attributed_evidence["metadata"]["written_by"] == {
        "agent": "claude-code",
        "session": "s1",
    }
    # Attribution must not move the tier. (Which tier a client-mode write gets is
    # the HTTP channel's policy, not this test's concern.)
    assert attributed_evidence["provenance"] == plain_evidence["provenance"]
    assert attributed_evidence["evidence_type"] == plain_evidence["evidence_type"]
    plain = a._storage.peek_memory(plain_id)
    assert stored["tags"] == plain["tags"]
    assert stored["source"] == plain["source"]

    intent = next(
        row
        for row in a._storage.get_active_intents(repo_id="proj-a", status="all")
        if row["id"] == a_intent
    )
    assert intent["context"]["written_by"] == {"agent": "claude-code", "session": "s1"}
    token = owner_token_path_for_request(shared_server, shared_server).read_text()
    headers = {"X-Visp-Owner-Token": token}
    report_url = f"{shared_server}/intents/{intent['id']}/workflow-status"
    # One agent starts the report and the other continues it: both reach the
    # server as the local owner, so the reporter binding holds across agents.
    payload = {
        "source": "ci", "task_id": "deploy", "event_id": "e1", "revision": 1,
        "status": "active", "summary": "Started by Claude Code",
    }
    first = requests.post(
        report_url, json=payload, headers={**headers, "X-Visp-Agent": "claude-code"}, timeout=5,
    )
    assert first.status_code == 200, first.text
    payload.update(event_id="e2", revision=2, summary="Continued by Codex")
    second = requests.post(
        report_url, json=payload, headers={**headers, "X-Visp-Agent": "codex"}, timeout=5,
    )
    assert second.status_code == 200, second.text

    a.record_utility_feedback(a_id, "used", query="deployment")
    assert a.inspect_utility_signals()["summary"]["total_events"] == 1
    assert a.verify_utility_signals()["valid"]
    assert a.reset_utility_signals() == 1
    monkeypatch.setattr(
        a,
        "check_conflict",
        lambda *args, **kwargs: ConflictVerdict.found(
            {
                "conflict": True,
                "reason": "The deployment changed.",
                "conflicting_ids": [a_id],
            }
        ),
    )
    successor_id = a.learn("Project A now deploys to green", detect_conflicts=True, reconcile=False)
    assert a._storage.peek_memory(a_id)["metadata"]["superseded_by"] == successor_id

    # The export carries each record's writer; the import round trip (and that it
    # keeps that writer under a different agent) is covered by
    # test_client_parity_admin.py.
    exported = a.export(tmp_path / "a.json")
    exported_row = next(
        row for rows in exported["memories"].values() for row in rows if row["id"] == a_id
    )
    assert exported_row["metadata"]["written_by"] == stored["metadata"]["written_by"]
    assert "proj-b" not in json.dumps(exported, default=str)

    scope = ReindexScope(repo_id="proj-a")
    assert a._storage.inspect_embedding_index(
        storage_backend="client", provider="noop", scope=scope
    ).matched_memories
    assert a._storage.rebuild_embedding_index(scope=scope, dry_run=True).dry_run
    assert requests.get(f"{shared_server}/platform/audit-log", timeout=5).status_code == 403
    events = a._storage.list_audit_logs(repo_id="proj-a")
    assert events
    assert a._storage.purge_memory(a_id)
    assert a._storage.peek_memory(a_id) is None
    capabilities = a._storage.get_capabilities()
    assert capabilities.audit_log and capabilities.reindex
    capability_response = requests.get(f"{shared_server}/diagnostics/capabilities", timeout=5)
    assert capability_response.json() == capabilities.to_dict()
    assert b_intent != a_intent
