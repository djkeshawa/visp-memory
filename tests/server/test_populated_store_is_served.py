"""The server answers with what is actually in the store it was pointed at.

LC-84 shipped because nothing tested the server against a store that already had
contents. Every server test built its store through the API as an admin, so the
one path a user takes — `visp-memory serve` in a project that has been recording
for weeks, read by the bundled dashboard — was never exercised. The store held
twelve memories and the dashboard read zero.

These tests set up that path deliberately: rows written directly to storage before
the server sees them, and the principal `serve` actually creates on a loopback bind
(anonymous, non-admin, no team — see `_ensure_serveable_auth_config`).
"""

import tempfile
import unittest.mock as mock
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from visp_memory.config import MemoryConfig, ServerConfig
from visp_memory.core.intent_evaluator import IntentEvaluator
from visp_memory.core.lifecycle import MemoryLifecycleManager
from visp_memory.core.model_router import ModelRouter
from visp_memory.core.storage import LocalStorage
from visp_memory.server.app import app
from visp_memory.server.auth_store import AuthStore

REPO_ID = "populated-project"
MEMORY_COUNT = 12


def _populate(storage: LocalStorage) -> list[str]:
    """Write a store the way weeks of `visp-memory record` would have."""
    memory_ids = [
        storage.store_memory(
            f"Populated memory number {index}",
            layer="episodic",
            category="note",
            repo_id=REPO_ID,
        )
        for index in range(MEMORY_COUNT)
    ]
    storage.set_intent("Ship the dashboard fix", repo_id=REPO_ID)
    storage.store_memory("Another project's memory", layer="episodic", repo_id="other-project")
    return memory_ids


@pytest_asyncio.fixture
async def served_store():
    """A client for a server started over a store that already has contents."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        data_dir = Path(tmpdir)
        storage = LocalStorage(data_dir)
        memory_ids = _populate(storage)

        app.state.storage = storage
        app.state.auth_store = AuthStore(data_dir / "auth.db")
        app.state.memory_lifecycle = MemoryLifecycleManager(storage, data_dir / "lifecycle.db")

        config = MemoryConfig(repo_id=REPO_ID)
        config.embedding.provider = "noop"
        # Exactly what `visp-memory serve` leaves behind on a loopback bind with no
        # credentials configured: auth on, anonymous allowed, no default team, and
        # local owner mode. ASGITransport presents a 127.0.0.1 peer, which is the
        # other half of what makes this principal the store's owner.
        config.server = ServerConfig(
            auth_enabled=True, allow_anonymous=True, local_owner_mode=True
        )
        app.state.model_router = ModelRouter(config.llm)
        app.state.intent_evaluator = IntentEvaluator(storage, app.state.model_router, config.llm)

        transport = httpx.ASGITransport(app=app)
        with (
            mock.patch("visp_memory.server.auth.load_config", return_value=config),
            mock.patch("visp_memory.server.routers.memories.load_config", return_value=config),
            mock.patch("visp_memory.server.routers.intents.load_config", return_value=config),
            mock.patch("visp_memory.server.app.config", config),
        ):
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                yield client, memory_ids


@pytest.mark.asyncio
async def test_status_counts_the_memories_the_store_holds(served_store):
    client, _ = served_store

    response = await client.get(f"/status?repo_id={REPO_ID}")

    assert response.status_code == 200
    body = response.json()
    assert body["stats_status"] == "ok"
    assert body["stats"]["total_memories"] == MEMORY_COUNT
    assert body["stats"]["memories_by_layer"] == {"episodic": MEMORY_COUNT}
    assert body["stats"]["active_intents"] == 1


@pytest.mark.asyncio
async def test_status_reports_the_keys_the_dashboard_reads(served_store):
    client, _ = served_store

    body = (await client.get(f"/status?repo_id={REPO_ID}")).json()

    # The dashboard reads these four off the status payload. The non-admin branch
    # of the stats path produced only two of them, so the other two rendered as 0
    # even when the counts it did produce were right.
    for key in ("total_memories", "active_intents", "memories_by_layer", "total_relationships"):
        assert key in body["stats"], key


@pytest.mark.asyncio
async def test_listing_memories_returns_the_rows_that_are_stored(served_store):
    client, memory_ids = served_store

    response = await client.get(f"/memories?repo_id={REPO_ID}&limit=200")

    assert response.status_code == 200
    returned = response.json()
    assert len(returned) == MEMORY_COUNT
    assert {memory["id"] for memory in returned} == set(memory_ids)


@pytest.mark.asyncio
async def test_listing_memories_stays_inside_the_requested_project(served_store):
    client, _ = served_store

    returned = (await client.get("/memories?repo_id=other-project")).json()

    assert [memory["content"] for memory in returned] == ["Another project's memory"]


@pytest.mark.asyncio
async def test_stats_are_not_capped_by_the_default_page_size(served_store):
    client, _ = served_store
    for index in range(60):
        app.state.storage.store_memory(
            f"Memory past the default page size {index}",
            layer="episodic",
            repo_id=REPO_ID,
        )

    body = (await client.get(f"/status?repo_id={REPO_ID}")).json()

    assert body["stats"]["total_memories"] == MEMORY_COUNT + 60


@pytest.mark.asyncio
async def test_the_intent_the_store_holds_is_listed(served_store):
    client, _ = served_store

    response = await client.get(f"/intents?repo_id={REPO_ID}")

    assert response.status_code == 200
    assert [intent["description"] for intent in response.json()] == ["Ship the dashboard fix"]


@pytest.mark.asyncio
async def test_a_status_request_with_no_query_falls_back_to_the_configured_project(
    served_store,
):
    client, _ = served_store

    response = await client.get("/status")

    assert response.status_code == 200
    assert response.json()["stats"]["total_memories"] == MEMORY_COUNT


@pytest.mark.asyncio
async def test_a_complete_scan_is_not_reported_as_truncated(served_store):
    client, _ = served_store

    body = (await client.get(f"/status?repo_id={REPO_ID}")).json()

    assert body["stats_status"] == "ok"
    assert body["stats"].get("truncated", False) is False
