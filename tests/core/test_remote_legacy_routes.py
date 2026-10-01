"""A missing optional route must not masquerade as a missing resource."""
from unittest.mock import Mock

import pytest

from tests.core.test_remote_storage import FakeResponse, remote_storage_with
from visp_memory.core.remote.errors import RemoteStorageError


def test_peek_missing_route_falls_back_to_memory_read():
    storage = remote_storage_with(FakeResponse(404, {"detail": "Not Found"}))
    memory = {"id": "belief", "repo_id": "repo-a", "authority": 0.9}
    storage.get_memory = Mock(return_value=memory)
    assert storage.peek_memory("belief") == memory
    storage.get_memory.assert_called_once_with("belief")


@pytest.mark.parametrize("payload", [{"detail": "Memory not found"}, {}, ["Not Found"]])
def test_peek_missing_resource_does_not_fall_back(payload):
    storage = remote_storage_with(FakeResponse(404, payload))
    storage.get_memory = Mock(side_effect=AssertionError("do not bypass a resource 404"))
    assert storage.peek_memory("missing") is None


def test_turn_keys_missing_route_falls_back_to_empty_hits():
    storage = remote_storage_with(FakeResponse(404, {"detail": "Not Found"}))
    assert storage.search_turn_keys("task") == []


@pytest.mark.parametrize("status,payload", [
    (404, {"detail": "Repository not found"}),
    (403, {"detail": "Forbidden"}),
    (500, {"detail": "Not Found"}),
])
def test_turn_keys_other_errors_are_not_suppressed(status, payload):
    storage = remote_storage_with(FakeResponse(status, payload))
    with pytest.raises(RemoteStorageError):
        storage.search_turn_keys("task")


def test_capabilities_legacy_fallback_is_retried_after_server_upgrade():
    storage = remote_storage_with(FakeResponse(404, {"detail": "Not Found"}))
    assert storage.get_capabilities().vector_search is False
    storage.session.response = FakeResponse(200, {"vector_search": True})
    assert storage.get_capabilities().vector_search is True
    assert len(storage.session.get_calls) == 2
    assert storage.get_capabilities().vector_search is True
    assert len(storage.session.get_calls) == 2


def test_legacy_peek_preserves_supersede_authority_comparison():
    from visp_memory import Memory

    storage = remote_storage_with(FakeResponse())

    def get(url, params=None):
        if url.endswith("/peek"):
            return FakeResponse(404, {"detail": "Not Found"})
        memory_id = url.rsplit("/", 1)[-1]
        return FakeResponse(200, {
            "id": memory_id, "repo_id": "repo-a",
            "source": "external" if memory_id == "new" else "authored",
        })

    storage.session.get = get
    facade = Memory.__new__(Memory)
    facade._storage = storage
    assert facade._may_supersede(superseding_id="new", superseded_id="old") is False


def test_task_brief_coverage_survives_legacy_missing_turn_key_route():
    from visp_memory.core.task_brief import TaskMemoryBriefCompiler

    storage = remote_storage_with(FakeResponse())

    def get(url, params=None):
        if url.endswith("/diagnostics/capabilities"):
            return FakeResponse(404, {"detail": "Not Found"})
        return FakeResponse(200, [])

    def post(url, json=None):
        storage.session.post_calls.append((url, json))
        if url.endswith("/turn-keys/search"):
            return FakeResponse(404, {"detail": "Not Found"})
        return FakeResponse(200, [])

    storage.session.get = get
    storage.session.post = post
    brief = TaskMemoryBriefCompiler(storage).prepare(
        "Review deployment", repo_id="repo-a", context_selection="coverage",
    )
    assert brief["citations"] == []
    assert brief["unknowns"]
    assert any(url.endswith("/turn-keys/search") for url, _ in storage.session.post_calls)
