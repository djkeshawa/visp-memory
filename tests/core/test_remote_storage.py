import pytest
import requests

from visp_memory.core.remote_storage import RemoteStorage, RemoteStorageError


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
        self.last_patch_url = None
        self.last_patch_json = None
        self.last_get_url = None

    def post(self, url, json=None):
        self.last_post_url = url
        self.last_post_json = json
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

    assert storage.list_memories(layer="semantic", category="fragile_area", repo_id=None) == []
    assert storage.session.last_get_url == "http://memory.example/memories"
    assert storage.session.last_get_params == {
        "layer": "semantic",
        "category": "fragile_area",
    }


def test_remote_storage_complete_intent_calls_intent_endpoint():
    storage = remote_storage_with(FakeResponse(200, {"status": "completed"}))

    assert storage.complete_intent("intent-1") is True
    assert storage.session.last_post_url == "http://memory.example/intents/intent-1/complete"
    assert storage.session.last_post_json is None


def test_remote_storage_complete_intent_returns_false_for_missing_intent():
    storage = remote_storage_with(FakeResponse(404, {"detail": "Intent not found"}))

    assert storage.complete_intent("missing") is False


def test_remote_storage_update_intent_calls_patch_endpoint():
    storage = remote_storage_with(FakeResponse(200, {"status": "updated"}))

    assert storage.update_intent("intent-1", description="New goal", priority=3) is True
    assert storage.session.last_patch_url == "http://memory.example/intents/intent-1"
    assert storage.session.last_patch_json == {"description": "New goal", "priority": 3}


def test_remote_storage_related_memories_calls_authenticated_api_contract():
    payload = [{"id": "memory-2", "relationship": "supports", "strength": 0.9}]
    storage = remote_storage_with(FakeResponse(200, payload))

    assert storage.get_related_memories("memory-1", relationship="supports") == payload
    assert storage.session.last_get_url == "http://memory.example/memories/memory-1/related"
    assert storage.session.last_get_params == {"relationship": "supports"}


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
    assert storage.start_session() == "session-1"
    assert storage.session.last_post_url == "http://memory.example/sessions"

    storage = remote_storage_with(FakeResponse(200, {"status": "completed"}))
    assert storage.end_session("session-1", "Done", ["memory-1"]) is True
    assert storage.session.last_post_url == "http://memory.example/sessions/session-1/complete"
    assert storage.session.last_post_json == {
        "summary": "Done",
        "memory_ids": ["memory-1"],
    }


def test_remote_storage_missing_session_returns_false():
    storage = remote_storage_with(FakeResponse(404, {"detail": "Session not found"}))
    assert storage.end_session("missing", "", []) is False
