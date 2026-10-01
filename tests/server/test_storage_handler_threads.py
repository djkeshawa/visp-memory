"""SQLite handlers run off the event loop and inherit request attribution."""
import threading

import pytest

from tests.server.test_owner_maintenance import owner_client as owner_client
from tests.server.test_portability import OWNER_HEADERS, graph_document
from visp_memory.core.attribution import current_writer
from visp_memory.server.app import app


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path,payload,operation", [
    ("GET", "/repos/repo-a/export", None, "list_memories"),
    ("POST", "/repos/repo-a/import", graph_document(), "import_graph"),
    ("GET", "/memories/{memory_id}/peek?repo_id=repo-a", None, "peek_memory"),
    ("POST", "/turn-keys/search", {"query": "observation", "repo_id": "repo-a"},
     "search_turn_keys"),
    ("GET", "/intents/usage?repo_id=repo-a", None, "inspect_intent_usage"),
    ("GET", "/repos/repo-a/registration", None, "inspect_repository_registration"),
    ("GET", "/diagnostics/capabilities", None, "get_capabilities"),
    ("POST", "/recall-events", {"memory_id": "{memory_id}", "repo_id": "repo-a",
                               "event_type": "surfaced"}, "log_recall_event"),
    ("GET", "/recall-events/utility?repo_id=repo-a", None, "inspect_recall_utility"),
    ("DELETE", "/recall-events?repo_id=repo-a", None, "reset_recall_utility"),
    ("GET", "/recall-events/verify?repo_id=repo-a", None, "verify_recall_utility"),
])
async def test_storage_handler_thread_inherits_writer(
    owner_client, monkeypatch, method, path, payload, operation,
):
    storage = app.state.storage
    memory_id = storage.store_memory("Observation", repo_id="repo-a", auto_link=False)
    event_thread = threading.get_ident()
    original = getattr(storage, operation)
    calls = []

    def observed(*args, **kwargs):
        calls.append((threading.get_ident(), current_writer()))
        return original(*args, **kwargs)

    monkeypatch.setattr(storage, operation, observed)
    if payload and payload.get("memory_id") == "{memory_id}":
        payload = {**payload, "memory_id": memory_id}
    response = await owner_client.request(
        method, path.format(memory_id=memory_id), json=payload,
        headers={**OWNER_HEADERS, "X-Visp-Agent": "audit-agent"},
    )
    assert response.status_code == 200, response.text
    assert calls
    assert all(thread != event_thread for thread, _ in calls)
    assert all(writer is not None and writer.agent == "audit-agent" for _, writer in calls)
