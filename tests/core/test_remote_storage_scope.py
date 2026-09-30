"""Every RemoteStorage call that carries an optional repo_id defaults to the client's.

A shared server refuses an unscoped request with 400 "repo_id is required", so a
client-mode method that forwards ``repo_id=None`` fails there even though the
client was configured for exactly one project. These tests pin, per method, that
the configured scope is sent when the caller names none and that an explicit
scope still wins.
"""

import pytest

from tests.core.test_remote_storage import FakeResponse, remote_storage_with

# (method, kwargs, response payload, how the scope is read back off the fake session)
READS = [
    ("search_memories", {"query": "q"}, [], "post"),
    ("list_memories", {}, [], "get"),
    ("get_active_intents", {}, [], "get"),
    ("get_all_relationships", {}, [], "get"),
    ("get_stats", {}, {}, "get"),
]
WRITES = [
    ("store_memory", {"content": "note"}, {"id": "m-1"}, "post"),
    ("set_intent", {"description": "goal"}, {"id": "i-1"}, "post"),
    ("start_session", {}, {"id": "s-1"}, "post"),
]


def _sent_repo_id(storage, channel):
    if channel == "get":
        return (storage.session.last_get_params or {}).get("repo_id")
    return (storage.session.last_post_json or {}).get("repo_id")


@pytest.mark.parametrize("method,kwargs,payload,channel", READS + WRITES)
def test_unscoped_call_sends_the_configured_repo(method, kwargs, payload, channel):
    storage = remote_storage_with(FakeResponse(200, payload))

    getattr(storage, method)(**kwargs)

    assert _sent_repo_id(storage, channel) == "repo-a"


@pytest.mark.parametrize("method,kwargs,payload,channel", READS + WRITES)
def test_explicit_repo_id_wins_over_the_configured_repo(method, kwargs, payload, channel):
    storage = remote_storage_with(FakeResponse(200, payload))

    getattr(storage, method)(repo_id="repo-b", **kwargs)

    assert _sent_repo_id(storage, channel) == "repo-b"


@pytest.mark.parametrize("method,kwargs,payload,channel", READS)
def test_unconfigured_client_still_sends_no_scope(method, kwargs, payload, channel):
    storage = remote_storage_with(FakeResponse(200, payload))
    storage.repo_id = None

    getattr(storage, method)(**kwargs)

    assert _sent_repo_id(storage, channel) is None
