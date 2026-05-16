import pytest
import requests

from llm_memory.core.remote_storage import RemoteStorage, RemoteStorageError


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

    def post(self, url, json):
        return self.response

    def get(self, url, params=None):
        self.last_get_params = params
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
