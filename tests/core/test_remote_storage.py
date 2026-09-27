from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
import requests

from visp_memory import Memory, MemoryConfig
from visp_memory.core.hybrid_retrieval import HybridRetriever
from visp_memory.core.owner_token import OWNER_TOKEN_HEADER
from visp_memory.core.recall_candidates import recall_candidates
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
        self.get_calls = []
        self.last_post_url = None
        self.last_post_json = None
        self.post_calls = []
        self.last_patch_url = None
        self.last_patch_json = None
        self.last_get_url = None
        self.last_delete_url = None
        self.last_delete_params = None
        self.delete_calls = []
        self.closed = False

    def post(self, url, json=None):
        self.last_post_url = url
        self.last_post_json = json
        self.post_calls.append((url, json))
        return self.response

    def get(self, url, params=None):
        self.last_get_url = url
        self.last_get_params = params
        self.get_calls.append((url, params))
        return self.response

    def patch(self, url, json=None):
        self.last_patch_url = url
        self.last_patch_json = json
        return self.response

    def delete(self, url, params=None):
        self.last_delete_url = url
        self.last_delete_params = params
        self.delete_calls.append((url, params))
        return self.response

    def close(self):
        self.closed = True


def remote_storage_with(response):
    storage = RemoteStorage.__new__(RemoteStorage)
    storage.server_url = "http://memory.example"
    storage.repo_id = "repo-a"
    storage.session = FakeSession(response)
    return storage


@pytest.mark.parametrize("has_partial_match", [False, True])
def test_recall_refill_respects_http_query_cap_and_returns_partial_results(has_partial_match):
    from pydantic import ValidationError

    from visp_memory.server.schemas import MAX_QUERY_LIMIT, SearchQuery

    class BoundedSession(FakeSession):
        def post(self, url, json=None):
            self.post_calls.append((url, json))
            try:
                query = SearchQuery(**json)
            except ValidationError:
                return FakeResponse(422, {"detail": "Query limit exceeded"})
            return FakeResponse(payload=[
                {"id": f"match-{index}", "repo_id": "repo-a", "content": "weak migration"}
                for index in range(query.limit)
            ])

    storage = remote_storage_with(FakeResponse())
    storage.session = BoundedSession(FakeResponse())
    accepted_id = f"match-{MAX_QUERY_LIMIT - 1}" if has_partial_match else None

    def rank_results(rows):
        return [row for row in rows if row["id"] == accepted_id]

    result = recall_candidates(
        storage, "migration", repo_id="repo-a", layers=["episodic"], limit=10,
        rank_results=rank_results,
    )

    assert [row["id"] for row in rank_results(result.allowed)] == (
        [accepted_id] if has_partial_match else []
    )
    limits = [payload["limit"] for _, payload in storage.session.post_calls]
    assert limits[-1] == MAX_QUERY_LIMIT
    assert all(limit <= MAX_QUERY_LIMIT for limit in limits)
    assert len(limits) < 10, "A capped remote query must terminate without endless refill"


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


def test_remote_memory_delete_and_purge_use_distinct_confirmation_contracts():
    storage = remote_storage_with(FakeResponse(204))

    assert storage.delete_memory("memory-1") is True
    assert storage.session.last_delete_url == "http://memory.example/memories/memory-1"
    assert storage.session.last_delete_params is None

    storage = remote_storage_with(FakeResponse(200, {"purged_ids": ["memory-1"]}))

    assert storage.purge_memory("memory-1") is True
    assert storage.session.last_delete_url == (
        "http://memory.example/memories/memory-1/purge"
    )
    assert storage.session.last_delete_params == {"confirmation": "memory-1"}


def test_remote_memory_delete_and_purge_report_missing_rows_without_success():
    for operation in ("delete_memory", "purge_memory"):
        storage = remote_storage_with(FakeResponse(404))

        assert getattr(storage, operation)("missing-memory") is False


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


def test_remote_storage_memory_listing_forwards_offset():
    storage = remote_storage_with(FakeResponse(200, []))

    assert storage.list_memories(repo_id="repo-a", limit=25, offset=50) == []
    assert storage.session.last_get_params == {
        "repo_id": "repo-a",
        "limit": 25,
        "offset": 50,
    }


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


class _ProbeSession:
    """Small requests.Session substitute that records the guarded probe call."""

    def __init__(self, response=None, error=None):
        self.headers = {}
        self.response = response or FakeResponse(200, {})
        self.error = error
        self.calls = []
        self.closed = False

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if self.error is not None:
            raise self.error
        if hasattr(self, "responses") and self.responses:
            response = self.responses.pop(0)
        else:
            response = self.response
        if hasattr(self, "on_response"):
            self.on_response(response)
        return response

    def get(self, url, params=None):
        return self.request("GET", url, params=params)

    def close(self):
        self.closed = True


def test_remote_constructor_prefers_jwt_sets_timeout_and_closes_idempotently(
    monkeypatch, caplog
):
    import visp_memory.core.remote_storage as remote_module

    session = _ProbeSession(FakeResponse(401, {"detail": "unauthorized"}))
    monkeypatch.setattr(remote_module.requests, "Session", lambda: session)

    with caplog.at_level("WARNING"):
        storage = RemoteStorage(
            "http://remote.example/memory/",
            api_key="api-key",
            jwt_token="jwt-token",
            timeout=2.5,
        )

    assert storage.server_url == "http://remote.example/memory"
    assert session.headers == {"Authorization": "Bearer jwt-token"}
    assert session.calls == [
        (
            "GET",
            "http://remote.example/memory/",
            {"params": None, "timeout": 2.5},
        )
    ]
    assert "cleartext HTTP" in caplog.text
    assert "Authentication" in caplog.text

    storage.close()
    storage.close()
    assert session.closed is True
    assert storage.session is None


def test_remote_constructor_survives_best_effort_probe_transport_failure(monkeypatch, caplog):
    import visp_memory.core.remote_storage as remote_module

    session = _ProbeSession(error=requests.ConnectionError("offline"))
    monkeypatch.setattr(remote_module.requests, "Session", lambda: session)

    with caplog.at_level("WARNING"):
        storage = RemoteStorage("https://memory.example", api_key="api-key")

    assert storage.session is session
    assert "Remote request GET https://memory.example/ failed" in caplog.text
    assert session.calls[0][2]["timeout"] == 30.0


def test_remote_storage_sends_owner_token_only_to_loopback_with_token_file(
    monkeypatch, tmp_path
):
    import visp_memory.core.remote_storage as remote_module

    token_dir = tmp_path / "run"
    token_dir.mkdir()
    (token_dir / "owner-8765.token").write_text("same-user-token", encoding="utf-8")
    monkeypatch.setattr("visp_memory.core.remote.owner_auth.run_dir", lambda: token_dir)
    sessions = []

    def create_session():
        session = _ProbeSession(FakeResponse(200, {}))
        sessions.append(session)
        return session

    monkeypatch.setattr(remote_module.requests, "Session", create_session)

    RemoteStorage("http://127.0.0.1:8765")
    RemoteStorage("http://memory.example:8765")
    RemoteStorage("http://127.0.0.1:8766")

    assert sessions[0].calls[0][2]["headers"][OWNER_TOKEN_HEADER] == "same-user-token"
    assert "headers" not in sessions[1].calls[0][2]
    assert "headers" not in sessions[2].calls[0][2]


def test_remote_storage_rereads_owner_token_and_retries_one_forbidden_request(
    monkeypatch, tmp_path
):
    import visp_memory.core.remote_storage as remote_module

    token_dir = tmp_path / "run"
    token_dir.mkdir()
    token_path = token_dir / "owner-8765.token"
    token_path.write_text("old-token", encoding="utf-8")
    monkeypatch.setattr("visp_memory.core.remote.owner_auth.run_dir", lambda: token_dir)
    session = _ProbeSession(FakeResponse(200, {}))
    monkeypatch.setattr(remote_module.requests, "Session", lambda: session)
    storage = RemoteStorage("http://localhost:8765")
    session.responses = [FakeResponse(403, {}), FakeResponse(200, {})]
    session.on_response = lambda response: (
        token_path.write_text("new-token", encoding="utf-8")
        if response.status_code == 403
        else None
    )

    response = storage.session.get("http://localhost:8765/maintenance/verify")

    assert response.status_code == 200
    assert session.calls[-2][2]["headers"][OWNER_TOKEN_HEADER] == "old-token"
    assert session.calls[-1][2]["headers"][OWNER_TOKEN_HEADER] == "new-token"
    assert session.calls[-1][2]["timeout"] == storage.timeout
    assert len(session.calls) == 3


@pytest.mark.parametrize(
    ("operation", "call"),
    [
        ("get schema status", lambda s: s.get_schema_status()),
        ("store evidence", lambda s: s.store_evidence("e", repo_id="repo-a")),
        ("list evidence", lambda s: s.list_evidence("repo-a")),
        ("attach evidence", lambda s: s.attach_evidence("belief", ["evidence"], repo_id="repo-a")),
        ("get memory", lambda s: s.get_memory("memory")),
        ("search memories", lambda s: s.search_memories("query")),
        ("list memories", lambda s: s.list_memories()),
        ("update memory", lambda s: s.update_memory("memory", content="new")),
        (
            "revise memory",
            lambda s: s.revise_memory("memory", "new", evidence_ids=["evidence"]),
        ),
        ("delete memory", lambda s: s.delete_memory("memory")),
        ("purge memory", lambda s: s.purge_memory("memory")),
        ("set intent", lambda s: s.set_intent("goal")),
        ("list intents", lambda s: s.get_active_intents()),
        ("update intent", lambda s: s.update_intent("intent", description="new")),
        (
            "append intent outcome",
            lambda s: s.append_intent_outcome("intent", {"outcome": "done"}),
        ),
        ("add relationship", lambda s: s.add_relationship("source", "target", "supports")),
        ("get related memories", lambda s: s.get_related_memories("memory")),
        ("list relationships", lambda s: s.get_all_relationships()),
        ("delete relationship", lambda s: s.delete_relationship("relationship")),
        ("start session", lambda s: s.start_session(repo_id="repo-a")),
        ("get session", lambda s: s.get_session("session")),
        ("complete session", lambda s: s.end_session("session", "done", [])),
        ("get stats", lambda s: s.get_stats()),
        ("store repository", lambda s: s.store_repository({"name": "repo"})),
        ("get repository", lambda s: s.get_repository("repo")),
        ("list repositories", lambda s: s.list_repositories()),
        ("archive repository", lambda s: s.update_repository("repo", status="archived")),
        ("purge repository", lambda s: s.purge_repository("repo")),
        ("list project scopes", lambda s: s.list_project_ids()),
        (
            "add repository dependency",
            lambda s: s.add_repo_dependency("repo", "dependency", "runtime"),
        ),
        ("get repository dependencies", lambda s: s.get_repo_dependencies("repo")),
        ("store user", lambda s: s.store_user({"username": "alice"})),
        ("get user", lambda s: s.get_user("user")),
        ("store team", lambda s: s.store_team({"name": "platform"})),
        ("get team", lambda s: s.get_team("team")),
        ("add team member", lambda s: s.add_team_member("team", "user")),
        ("get user teams", lambda s: s.get_user_teams("user")),
    ],
)
def test_remote_endpoints_turn_http_failures_into_operation_errors(operation, call):
    storage = remote_storage_with(FakeResponse(500, {"detail": "backend unavailable"}))

    with pytest.raises(RemoteStorageError) as exc:
        call(storage)

    assert operation in str(exc.value)
    assert "HTTP 500: backend unavailable" in str(exc.value)


@pytest.mark.parametrize(
    ("operation", "call"),
    [
        ("get evidence", lambda s: s.get_evidence("missing")),
        ("get memory", lambda s: s.get_memory("missing")),
        ("update memory", lambda s: s.update_memory("missing", content="new")),
        ("delete memory", lambda s: s.delete_memory("missing")),
        ("update intent", lambda s: s.update_intent("missing", description="new")),
        ("append intent outcome", lambda s: s.append_intent_outcome("missing", {"outcome": "x"})),
        ("delete relationship", lambda s: s.delete_relationship("missing")),
        ("get session", lambda s: s.get_session("missing")),
        ("get repository", lambda s: s.get_repository("missing")),
        ("update repository", lambda s: s.update_repository("missing", status="archived")),
        ("get user", lambda s: s.get_user("missing")),
        ("get team", lambda s: s.get_team("missing")),
        ("add team member", lambda s: s.add_team_member("missing", "user")),
    ],
)
def test_remote_not_found_contracts_are_explicit(operation, call):
    storage = remote_storage_with(FakeResponse(404, {"detail": f"{operation} missing"}))

    result = call(storage)

    if operation.startswith("get "):
        assert result is None
    else:
        assert result is False


def test_remote_not_found_relationship_and_session_completion_contracts():
    storage = remote_storage_with(FakeResponse(404, {"detail": "missing"}))
    assert storage.get_related_memories("missing") == []
    assert storage.get_all_relationships() == []
    assert storage.end_session("missing", "", []) is SessionCompletionStatus.NOT_FOUND


def test_remote_session_completion_and_purge_use_distinct_status_contracts():
    storage = remote_storage_with(FakeResponse(409, {"detail": "already completed"}))
    assert storage.end_session("session", "done", []) is SessionCompletionStatus.ALREADY_COMPLETED

    storage = remote_storage_with(FakeResponse(404, {"detail": "missing"}))
    assert storage.purge_memory("missing") is False

    storage = remote_storage_with(FakeResponse(200, {"purged_ids": ["other"]}))
    assert storage.purge_memory("memory") is False
    assert storage.session.last_delete_url.endswith("/memories/memory/purge")
    assert storage.session.last_delete_params == {"confirmation": "memory"}

    storage = remote_storage_with(FakeResponse(200, {"purged_ids": ["memory"]}))
    assert storage.purge_memory("memory") is True


def test_remote_repository_purge_preserves_server_incomplete_detail():
    detail = {
        "repo_id": "repo-a",
        "status": "incomplete",
        "purged_memory_count": 2,
        "failed_memory_ids": ["memory-3"],
        "residual": {"memories": 1},
        "errors": ["memory-3: timeout"],
    }
    storage = remote_storage_with(FakeResponse(409, {"detail": detail}))

    assert storage.purge_repository("repo-a") == detail

    storage = remote_storage_with(FakeResponse(404, {"detail": "missing"}))
    assert storage.purge_repository("repo-a")["status"] == "not_found"


def test_remote_repository_purge_rejects_success_without_purged_status():
    storage = remote_storage_with(FakeResponse(200, {"repo_id": "repo-a", "status": "incomplete"}))

    result = storage.purge_repository("repo-a")

    assert result["status"] == "incomplete"
    assert result["repo_id"] == "repo-a"


class _MalformedResponse(FakeResponse):
    def json(self):
        raise ValueError("malformed")


def test_remote_response_helpers_reject_malformed_and_empty_payloads():
    storage = remote_storage_with(_MalformedResponse())
    with pytest.raises(RemoteStorageError, match="malformed JSON"):
        storage.list_memories()
    storage = remote_storage_with(FakeResponse(200, {}))
    with pytest.raises(RemoteStorageError, match="without returning an id"):
        storage.store_memory("memory")

    for payload in ({"id": ""}, {"id": None}):
        storage = remote_storage_with(FakeResponse(200, payload))
        with pytest.raises(RemoteStorageError, match="empty id"):
            storage.store_repository({"name": "repo"})


def test_remote_write_error_uses_text_when_error_body_has_no_detail():
    response = _MalformedResponse(502, text="upstream unavailable")
    storage = remote_storage_with(response)

    with pytest.raises(RemoteStorageError, match="HTTP 502: upstream unavailable"):
        storage.store_memory("memory")

    response = FakeResponse(502, payload=[], text="upstream unavailable")
    storage = remote_storage_with(response)
    with pytest.raises(RemoteStorageError, match="HTTP 502"):
        storage.store_memory("memory")


def test_remote_search_serializes_nested_datetime_values():
    storage = remote_storage_with(FakeResponse(200, []))
    nested = [datetime(2026, 1, 1, tzinfo=timezone.utc), ("prod",)]

    assert storage.search_memories("query", environment=nested) == []
    assert storage.session.last_post_json["environment"] == [
        "2026-01-01T00:00:00+00:00",
        ["prod"],
    ]


def test_remote_capabilities_and_collection_contract_are_explicit():
    storage = remote_storage_with(FakeResponse(200, {}))

    assert storage.get_capabilities().vector_search is False
    assert storage.get_capabilities().audit_log is False
    assert storage.get_collection("semantic") is None


def test_remote_storage_peek_uses_client_scope_without_counting_an_access():
    memory = {
        "id": "memory-1",
        "repo_id": "repo-a",
        "content": "Scoped memory",
        "access_count": 0,
    }
    storage = remote_storage_with(FakeResponse(200, memory))

    assert storage.peek_memory("memory-1") == memory
    assert storage.session.last_get_url == (
        "http://memory.example/memories/memory-1/peek"
    )
    assert storage.session.last_get_params == {"repo_id": "repo-a"}

    storage = remote_storage_with(FakeResponse(404, {"detail": "Memory not found"}))
    assert storage.peek_memory("other-repo-memory") is None


def test_remote_storage_recall_utility_methods_preserve_local_shapes():
    storage = remote_storage_with(FakeResponse(200, {"id": "event-1"}))

    assert storage.log_recall_event(
        "memory-1",
        "used",
        repo_id="repo-a",
        query="private query",
        task_id="task-1",
        outcome="helpful",
        metadata={"source": "test"},
    ) == "event-1"
    assert storage.session.last_post_url == "http://memory.example/recall-events"
    assert storage.session.last_post_json == {
        "memory_id": "memory-1",
        "event_type": "used",
        "repo_id": "repo-a",
        "query": "private query",
        "task_id": "task-1",
        "outcome": "helpful",
        "metadata": {"source": "test"},
    }

    report = {
        "summary": {"total_events": 1, "by_event_type": {"used": 1}, "memories": 1},
        "signals": [{"memory_id": "memory-1", "repo_id": "repo-a"}],
        "events": [{"id": "event-1", "memory_id": "memory-1", "repo_id": "repo-a"}],
        "verification": {
            "valid": True,
            "checked_events": 1,
            "cross_repository_events": 0,
            "violations": [],
        },
    }
    storage = remote_storage_with(FakeResponse(200, report))
    assert storage.inspect_recall_utility(
        memory_id="memory-1", repo_id="repo-a", event_type="used", limit=7
    ) == report
    assert storage.session.last_get_url == (
        "http://memory.example/recall-events/utility"
    )
    assert storage.session.last_get_params == {
        "memory_id": "memory-1",
        "repo_id": "repo-a",
        "event_type": "used",
        "limit": 7,
    }

    verification = report["verification"]
    storage = remote_storage_with(FakeResponse(200, verification))
    assert storage.verify_recall_utility(
        memory_id="memory-1", repo_id="repo-a", event_type="used"
    ) == verification
    assert storage.session.last_get_url == (
        "http://memory.example/recall-events/verify"
    )
    assert storage.session.last_get_params == {
        "memory_id": "memory-1",
        "repo_id": "repo-a",
        "event_type": "used",
    }

    storage = remote_storage_with(FakeResponse(200, {"deleted": 3}))
    assert storage.reset_recall_utility(
        memory_id="memory-1", repo_id="repo-a", event_type="used"
    ) == 3
    assert storage.session.last_delete_url == "http://memory.example/recall-events"
    assert storage.session.last_delete_params == {
        "memory_id": "memory-1",
        "repo_id": "repo-a",
        "event_type": "used",
    }


def test_remote_storage_recall_reads_default_to_the_client_repository():
    storage = remote_storage_with(FakeResponse(200, {"deleted": 0}))

    assert storage.reset_recall_utility() == 0
    assert storage.session.last_delete_params == {"repo_id": "repo-a"}


def test_remote_storage_searches_turn_keys_in_the_requested_repository():
    hits = [
        {
            "memory": {"id": "memory-1", "repo_id": "repo-a"},
            "span": "A useful turn",
            "similarity": 0.9,
        }
    ]
    storage = remote_storage_with(FakeResponse(200, hits))

    assert storage.search_turn_keys(
        "useful", repo_id="repo-a", limit=4, status="active"
    ) == hits
    assert storage.session.last_post_url == "http://memory.example/turn-keys/search"
    assert storage.session.last_post_json == {
        "query": "useful",
        "repo_id": "repo-a",
        "limit": 4,
        "status": "active",
    }


def test_remote_storage_reads_scoped_intent_and_registration_inspections():
    usage = {
        "exists": True,
        "memories": 2,
        "active_intents": 1,
        "total_intents": 1,
    }
    storage = remote_storage_with(FakeResponse(200, usage))

    assert storage.inspect_intent_usage() == usage
    assert storage.session.last_get_url == "http://memory.example/intents/usage"
    assert storage.session.last_get_params == {"repo_id": "repo-a"}

    registration = {
        "exists": True,
        "project_scopes": ["repo-a"],
        "unregistered_scopes": [],
    }
    storage = remote_storage_with(FakeResponse(200, registration))

    assert storage.inspect_repository_registration() == registration
    assert storage.session.last_get_url == (
        "http://memory.example/repos/repo-a/registration"
    )


def test_remote_capabilities_are_loaded_once_and_keep_server_flags():
    server_capabilities = {
        "graph": False,
        "vector_search": True,
        "repositories": False,
        "teams": False,
        "sessions": False,
        "audit_log": True,
        "reindex": True,
        "complete_graph_export": True,
        "atomic_graph_import": True,
    }
    storage = remote_storage_with(FakeResponse(200, server_capabilities))

    first = storage.get_capabilities()
    second = storage.get_capabilities()

    assert first is second
    assert first.to_dict() == server_capabilities
    assert storage.session.get_calls == [
        ("http://memory.example/diagnostics/capabilities", None)
    ]


def test_remote_capabilities_cache_the_legacy_404_fallback():
    storage = remote_storage_with(FakeResponse(404, {"detail": "Not found"}))

    first = storage.get_capabilities()
    second = storage.get_capabilities()

    assert first is second
    assert first.vector_search is False
    assert first.audit_log is False
    assert first.reindex is False
    assert storage.session.get_calls == [
        ("http://memory.example/diagnostics/capabilities", None)
    ]
