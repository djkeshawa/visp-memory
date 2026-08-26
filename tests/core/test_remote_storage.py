from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
import requests

from visp_memory import Memory, MemoryConfig
from visp_memory.core.hybrid_retrieval import HybridRetriever
from visp_memory.core.remote_storage import RemoteStorage, RemoteStorageError
from visp_memory.core.storage import EvidenceImmutableError, SessionCompletionStatus
from visp_memory.layers.episodic import EpisodicMemory
from visp_memory.layers.semantic import SemanticMemory
from visp_memory.recall.graph import GraphRecall
from visp_memory.recall.proactive import ProactiveRecall


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            error = requests.HTTPError(f"HTTP {self.status_code}")
            error.response = self
            raise error


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.last_get_params = None
        self.last_post_url = None
        self.last_post_json = None
        self.post_calls = []
        self.last_patch_url = None
        self.last_patch_json = None
        self.last_get_url = None

    def post(self, url, json=None):
        self.last_post_url = url
        self.last_post_json = json
        self.post_calls.append((url, json))
        return self.response

    def get(self, url, params=None):
        self.last_get_url = url
        self.last_get_params = params
        return self.response

    def patch(self, url, json=None):
        self.last_patch_url = url
        self.last_patch_json = json
        return self.response


def remote_storage_with(response):
    storage = RemoteStorage.__new__(RemoteStorage)
    storage.server_url = "http://memory.example"
    storage.session = FakeSession(response)
    return storage


def test_remote_revision_uses_plural_public_endpoint():
    storage = remote_storage_with(FakeResponse(payload={"id": "successor"}))

    assert storage.revise_memory(
        "old-memory", "New content", evidence_ids=["new-evidence"]
    ) == "successor"

    assert storage.session.last_post_url.endswith(
        "/memories/old-memory/revisions"
    )


def test_remote_intent_outcome_uses_single_atomic_endpoint():
    storage = remote_storage_with(FakeResponse(payload={"outcome_recorded": True}))

    assert storage.append_intent_outcome(
        "intent-1", {"outcome": "completed", "actor_id": "caller"}
    )

    assert storage.session.last_post_url == (
        "http://memory.example/intents/intent-1/outcomes"
    )
    assert storage.session.last_post_json == {"outcome": "completed"}


@pytest.mark.parametrize(
    ("operation", "call"),
    [
        ("store memory", lambda storage: storage.store_memory("remember this")),
        ("set intent", lambda storage: storage.set_intent("ship release")),
        (
            "add relationship",
            lambda storage: storage.add_relationship("source", "target", "related"),
        ),
        ("store repository", lambda storage: storage.store_repository({"name": "repo"})),
        (
            "add repository dependency",
            lambda storage: storage.add_repo_dependency("repo-a", "repo-b", "runtime"),
        ),
        ("store user", lambda storage: storage.store_user({"username": "alice"})),
        ("store team", lambda storage: storage.store_team({"name": "platform"})),
    ],
)
def test_remote_storage_write_failures_raise_clear_exception(operation, call):
    storage = remote_storage_with(FakeResponse(500, {"detail": "write failed"}))

    with pytest.raises(RemoteStorageError) as exc:
        call(storage)

    message = str(exc.value)
    assert operation in message
    assert "HTTP 500: write failed" in message
    assert "error_id" not in message


def test_remote_storage_write_requires_returned_id():
    storage = remote_storage_with(FakeResponse(200, {"status": "created"}))

    with pytest.raises(RemoteStorageError, match="without returning an id"):
        storage.store_memory("remember this")


def test_remote_storage_evidence_contract_and_immutability():
    storage = remote_storage_with(FakeResponse(200, {"id": "evidence-1"}))

    assert storage.store_evidence(
        "Exact tool output",
        repo_id="repo-a",
        evidence_type="tool_output",
        provenance="derived",
        metadata={"tool": "pytest"},
    ) == "evidence-1"
    assert storage.session.last_post_url == "http://memory.example/evidence"
    assert storage.session.last_post_json == {
        "content": "Exact tool output",
        "repo_id": "repo-a",
        "evidence_type": "tool_output",
        "provenance": "derived",
        "metadata": {"tool": "pytest"},
    }

    with pytest.raises(EvidenceImmutableError):
        storage.update_evidence("evidence-1", content="changed")
    assert storage.session.last_patch_url is None


def test_remote_storage_reads_evidence_and_server_schema_status():
    evidence = {
        "id": "evidence-1",
        "content": "Exact output",
        "content_hash": "abc",
        "repo_id": "repo-a",
        "evidence_type": "tool_output",
        "provenance": "derived",
        "metadata": {},
        "created_at": "2026-01-01T00:00:00+00:00",
        "record_type": "evidence",
    }
    storage = remote_storage_with(FakeResponse(200, evidence))
    assert storage.get_evidence("evidence-1") == evidence
    assert storage.session.last_get_url == "http://memory.example/evidence/evidence-1"

    storage = remote_storage_with(FakeResponse(200, [evidence]))
    assert storage.list_evidence(repo_id="repo-a") == [evidence]
    assert storage.session.last_get_params == {"repo_id": "repo-a", "limit": 10000}

    schema = {"current_version": 3, "stored_version": 3, "status": "ready"}
    storage = remote_storage_with(FakeResponse(200, {"schema_status": schema}))
    assert storage.get_schema_status() == schema
    assert storage.session.last_get_url == "http://memory.example/diagnostics/storage"


def test_remote_storage_forwards_belief_evidence_ids():
    storage = remote_storage_with(
        FakeResponse(
            200,
            {
                "id": "belief-1",
                "category": "fact",
                "belief_type": "fact",
                "epistemic_status": "inferred",
            },
        )
    )

    assert storage.store_memory(
        "Authentication requires secure cookies",
        layer="semantic",
        repo_id="repo-a",
        evidence_ids=["evidence-1"],
    ) == "belief-1"
    assert storage.session.last_post_json["evidence_ids"] == ["evidence-1"]


def test_remote_semantic_write_closes_input_and_forwards_authority_candidate():
    storage = remote_storage_with(
        FakeResponse(
            200,
            {
                "id": "belief-1",
                "category": "prohibition",
                "belief_type": "prohibition",
                "epistemic_status": "observed",
            },
        )
    )

    assert storage.store_memory(
        "Never bypass review",
        layer="semantic",
        category="prohibition",
        authority_attestation="opaque-envelope",
    ) == "belief-1"
    assert storage.session.last_post_json["authority_attestation"] == "opaque-envelope"

    for invalid in (
        {"category": "fact"},
        {"category": "fact", "epistemic_status": "observed"},
    ):
        with pytest.raises(RemoteStorageError):
            storage.store_memory("Rejected", layer="semantic", **invalid)


@pytest.mark.parametrize(
    "response_payload",
    [
        {"id": "belief-1"},
        {
            "id": "belief-1",
            "category": "procedure",
            "belief_type": "procedure",
            "epistemic_status": "inferred",
        },
        {
            "id": "belief-1",
            "category": "fact",
            "belief_type": "fact",
            "epistemic_status": "observed",
        },
    ],
)
def test_remote_semantic_write_refuses_old_or_mismatching_server_response(
    response_payload,
):
    storage = remote_storage_with(FakeResponse(200, response_payload))

    with pytest.raises(RemoteStorageError, match="semantic belief contract"):
        storage.store_memory("Governed fact", layer="semantic", category="fact")


def test_remote_storage_attaches_evidence_through_authorized_http_endpoint():
    storage = remote_storage_with(FakeResponse(200, {"id": "belief-1"}))

    storage.attach_evidence("belief-1", ["evidence-2"], repo_id="repo-a")

    assert storage.session.last_post_url == (
        "http://memory.example/memories/belief-1/evidence"
    )
    assert storage.session.last_post_json == {
        "repo_id": "repo-a",
        "evidence_ids": ["evidence-2"],
    }


def test_remote_storage_get_stats_accepts_repo_id():
    storage = remote_storage_with(FakeResponse(200, {"stats": {"total_memories": 3}}))

    assert storage.get_stats(repo_id="repo-a") == {"total_memories": 3}
    assert storage.session.last_get_params == {"repo_id": "repo-a"}


def test_remote_storage_search_maps_layer_to_api_layers_payload():
    storage = remote_storage_with(FakeResponse(200, []))

    assert storage.search_memories("auth", layer="episodic", limit=5, repo_id="repo-a") == []
    assert storage.session.last_post_url == "http://memory.example/recall"
    assert storage.session.last_post_json == {
        "query": "auth",
        "repo_id": "repo-a",
        "limit": 5,
        "layers": ["episodic"],
    }


def test_remote_storage_search_forwards_runtime_scope_and_serializes_datetime():
    storage = remote_storage_with(FakeResponse(200, []))
    as_of = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)

    assert storage.search_memories(
        "auth",
        repo_id="repo-a",
        environment=["prod", "staging"],
        task_type="deploy",
        as_of=as_of,
    ) == []
    assert storage.session.last_post_json == {
        "query": "auth",
        "repo_id": "repo-a",
        "environment": ["prod", "staging"],
        "task_type": "deploy",
        "as_of": "2026-01-15T12:00:00+00:00",
    }


def test_memory_recall_forwards_scope_before_remote_server_filter(tmp_path):
    scoped = {
        "id": "scoped-1",
        "content": "Production authentication deployment",
        "layer": "semantic",
        "category": "fact",
        "repo_id": "repo-a",
        "metadata": {"environment": "prod", "task_type": "deploy"},
        "tags": [],
    }

    class ScopedSession(FakeSession):
        def post(self, url, json=None):
            self.last_post_url = url
            self.last_post_json = json
            if json.get("environment") == "prod" and json.get("task_type") == "deploy":
                return FakeResponse(200, [scoped])
            return FakeResponse(200, [])

    remote = RemoteStorage.__new__(RemoteStorage)
    remote.server_url = "http://memory.example"
    remote.session = ScopedSession(FakeResponse(200, []))
    config = MemoryConfig(repo_id="repo-a")
    config.storage.data_dir = tmp_path
    config.embedding.provider = "noop"
    memory = Memory(config=config)
    memory._storage = remote

    results = memory.recall(
        "authentication deployment",
        environment="prod",
        task_type="deploy",
        as_of=datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc),
        min_score=0.0,
    )

    assert [item["id"] for item in results] == ["scoped-1"]
    assert results[0]["metadata"] == scoped["metadata"]
    assert remote.session.last_post_json["environment"] == "prod"
    assert remote.session.last_post_json["task_type"] == "deploy"
    assert remote.session.last_post_json["as_of"] == "2026-01-15T12:00:00+00:00"


def test_hybrid_retriever_forwards_runtime_scope_to_remote_direct_search():
    storage = remote_storage_with(FakeResponse(200, []))
    as_of = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)

    assert HybridRetriever(storage).retrieve(
        "authentication deployment",
        repo_id="repo-a",
        environment=["prod", "staging"],
        task_type="deploy",
        as_of=as_of,
    ) == []

    recall_payloads = [
        payload for url, payload in storage.session.post_calls if url.endswith("/recall")
    ]
    assert len(recall_payloads) == 4
    assert all(payload["environment"] == ["prod", "staging"] for payload in recall_payloads)
    assert all(payload["task_type"] == "deploy" for payload in recall_payloads)
    assert all(payload["as_of"] == "2026-01-15T12:00:00+00:00" for payload in recall_payloads)


def test_graph_recall_forwards_runtime_scope_to_remote_seed_search():
    storage = remote_storage_with(FakeResponse(200, []))
    as_of = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)

    result = GraphRecall(storage).trace(
        "authentication deployment",
        repo_id="repo-a",
        environment="prod",
        task_type=["deploy", "review"],
        as_of=as_of,
    )

    assert result["nodes"] == []
    assert storage.session.last_post_json["environment"] == "prod"
    assert storage.session.last_post_json["task_type"] == ["deploy", "review"]
    assert storage.session.last_post_json["as_of"] == "2026-01-15T12:00:00+00:00"


def test_proactive_searches_forward_runtime_scope_to_remote_layers():
    storage = remote_storage_with(FakeResponse(200, []))
    memory = SimpleNamespace(
        config=SimpleNamespace(repo_id="repo-a"),
        episodic=EpisodicMemory(storage),
        semantic=SemanticMemory(storage),
        rank_with_context=lambda memories, **kwargs: memories,
    )
    as_of = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)

    assert ProactiveRecall(
        memory,
        environment=["prod", "staging"],
        task_type="deploy",
        as_of=as_of,
    ).on_error("authentication failed") == []

    recall_payloads = [
        payload for url, payload in storage.session.post_calls if url.endswith("/recall")
    ]
    assert [payload["layers"] for payload in recall_payloads] == [
        ["episodic"],
        ["semantic"],
    ]
    assert all(payload["environment"] == ["prod", "staging"] for payload in recall_payloads)
    assert all(payload["task_type"] == ["deploy"] for payload in recall_payloads)
    assert all(payload["as_of"] == "2026-01-15T12:00:00+00:00" for payload in recall_payloads)


def test_remote_storage_add_relationship_sends_evidence_payload():
    storage = remote_storage_with(FakeResponse(200, {"id": "rel-1"}))

    assert (
        storage.add_relationship(
            "source",
            "target",
            "observed_in",
            strength=0.8,
            evidence={
                "confidence": "observed",
                "confidence_score": 0.9,
                "source": "api",
                "reason": "Remote client supplied evidence.",
            },
        )
        == "rel-1"
    )

    assert storage.session.last_post_url == "http://memory.example/relationships"
    assert storage.session.last_post_json == {
        "source_id": "source",
        "target_id": "target",
        "relationship": "observed_in",
        "strength": 0.8,
        "evidence": {
            "confidence": "observed",
            "confidence_score": 0.9,
            "source": "api",
            "reason": "Remote client supplied evidence.",
        },
    }


def test_remote_storage_get_relationships_preserves_evidence_payload():
    payload = [
        {
            "id": "rel-1",
            "source_id": "source",
            "target_id": "target",
            "relationship": "observed_in",
            "strength": 0.8,
            "evidence": {"confidence": "observed", "source": "api"},
        }
    ]
    storage = remote_storage_with(FakeResponse(200, payload))

    assert storage.get_all_relationships(repo_id="repo-a") == payload
    assert storage.session.last_get_url == "http://memory.example/relationships"
    assert storage.session.last_get_params == {"repo_id": "repo-a"}


def test_remote_storage_list_sends_supported_filters_without_none_values():
    storage = remote_storage_with(FakeResponse(200, []))

    assert storage.list_memories(layer="semantic", category="negative", repo_id=None) == []
    assert storage.session.last_get_url == "http://memory.example/memories"
    assert storage.session.last_get_params == {
        "layer": "semantic",
        "category": "negative",
    }


def test_remote_storage_complete_intent_is_ineffective_without_network_call():
    storage = remote_storage_with(FakeResponse(200, {"status": "completed"}))

    assert storage.complete_intent("intent-1") is False
    assert storage.session.last_post_url is None


def test_remote_storage_complete_intent_does_not_probe_missing_intent():
    storage = remote_storage_with(FakeResponse(404, {"detail": "Intent not found"}))

    assert storage.complete_intent("missing") is False
    assert storage.session.last_post_url is None


def test_remote_storage_update_intent_calls_patch_endpoint():
    storage = remote_storage_with(FakeResponse(200, {"status": "updated"}))

    assert storage.update_intent("intent-1", description="New goal", priority=3) is True
    assert storage.session.last_patch_url == "http://memory.example/intents/intent-1"
    assert storage.session.last_patch_json == {"description": "New goal", "priority": 3}


def test_remote_storage_update_intent_omits_status():
    storage = remote_storage_with(FakeResponse(200, {"status": "updated"}))

    assert storage.update_intent("intent-1", status="completed") is False
    assert storage.session.last_patch_url is None

    assert storage.update_intent(
        "intent-1", description="New goal", status="completed"
    ) is True
    assert storage.session.last_patch_json == {"description": "New goal"}


def test_remote_storage_related_memories_calls_authenticated_api_contract():
    payload = [{"id": "memory-2", "relationship": "supports", "strength": 0.9}]
    storage = remote_storage_with(FakeResponse(200, payload))

    as_of = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    assert storage.get_related_memories(
        "memory-1",
        relationship="supports",
        environment=["prod", "staging"],
        task_type="deploy",
        as_of=as_of,
    ) == payload
    assert storage.session.last_get_url == "http://memory.example/memories/memory-1/related"
    assert storage.session.last_get_params == {
        "relationship": "supports",
        "environment": ["prod", "staging"],
        "task_type": "deploy",
        "as_of": "2026-01-15T12:00:00+00:00",
    }


def test_remote_storage_related_memories_returns_empty_only_for_404():
    storage = remote_storage_with(FakeResponse(404, {"detail": "Memory not found"}))
    assert storage.get_related_memories("missing") == []

    storage = remote_storage_with(FakeResponse(500, {"detail": "database unavailable"}))
    with pytest.raises(RemoteStorageError, match="HTTP 500: database unavailable"):
        storage.get_related_memories("memory-1")


def test_remote_storage_malformed_read_response_is_explicit_error():
    storage = remote_storage_with(FakeResponse(200, {"unexpected": "object"}))

    with pytest.raises(RemoteStorageError, match="invalid response"):
        storage.list_memories()


def test_remote_storage_session_round_trip_contract():
    storage = remote_storage_with(FakeResponse(200, {"id": "session-1"}))
    assert storage.start_session(repo_id="repo-a") == "session-1"
    assert storage.session.last_post_url == "http://memory.example/sessions"
    assert storage.session.last_post_json == {"repo_id": "repo-a"}

    storage = remote_storage_with(FakeResponse(200, {"status": "completed"}))
    assert (
        storage.end_session("session-1", "Done", ["memory-1"])
        is SessionCompletionStatus.COMPLETED
    )
    assert storage.session.last_post_url == "http://memory.example/sessions/session-1/complete"
    assert storage.session.last_post_json == {
        "summary": "Done",
        "memory_ids": ["memory-1"],
    }


def test_remote_storage_missing_session_returns_typed_status():
    storage = remote_storage_with(FakeResponse(404, {"detail": "Session not found"}))
    assert (
        storage.end_session("missing", "", [])
        is SessionCompletionStatus.NOT_FOUND
    )


def test_remote_storage_get_session_contract():
    payload = {
        "id": "session-1",
        "owner_id": "alice",
        "team_id": "team-a",
        "repo_id": "repo-a",
        "memory_ids": [],
    }
    storage = remote_storage_with(FakeResponse(200, payload))

    assert storage.get_session("session-1") == payload
    assert storage.session.last_get_url == "http://memory.example/sessions/session-1"
