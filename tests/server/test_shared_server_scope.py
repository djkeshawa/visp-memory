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
from visp_memory.server import app as server_app
from visp_memory.server.app import app
from visp_memory.server.auth_store import AuthStore
from visp_memory.server.request_scope import request_repo_id


@pytest.mark.parametrize(
    ("shared", "expected"),
    [(True, None), (False, "leak")],
)
def test_request_scope_only_falls_back_for_a_non_shared_server(shared, expected):
    config = MemoryConfig(repo_id="leak")
    config.server.shared = shared

    assert request_repo_id(None, config) == expected
    assert request_repo_id("requested", config) == "requested"


@pytest_asyncio.fixture
async def scoped_server(tmp_path: Path, request):
    shared = request.param
    storage = LocalStorage(tmp_path)
    storage.store_memory("must not leak", repo_id="leak")
    app.state.storage = storage
    app.state.auth_store = AuthStore(tmp_path / "auth.db")
    app.state.memory_lifecycle = MemoryLifecycleManager(storage, tmp_path / "lifecycle.db")

    config = MemoryConfig(repo_id="leak")
    config.embedding.provider = "noop"
    config.server = ServerConfig(auth_enabled=False, shared=shared)
    app.state.model_router = ModelRouter(config.llm)
    app.state.intent_evaluator = IntentEvaluator(storage, app.state.model_router, config.llm)

    transport = httpx.ASGITransport(app=app)
    with (
        mock.patch("visp_memory.server.auth.load_config", return_value=config),
        mock.patch("visp_memory.server.routers.memories.load_config", return_value=config),
        mock.patch.object(server_app, "config", config),
    ):
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            yield client, storage


@pytest.mark.parametrize("scoped_server", [True], indirect=True)
@pytest.mark.asyncio
async def test_shared_routes_refuse_missing_scope_before_reading_or_writing_leak(
    scoped_server,
):
    client, storage = scoped_server
    with (
        mock.patch.object(storage, "list_memories", wraps=storage.list_memories) as read,
        mock.patch.object(storage, "store_memory", wraps=storage.store_memory) as write,
    ):
        read_response = await client.get("/memories")
        write_response = await client.post("/memories", json={"content": "must be refused"})

    assert read_response.status_code == 400
    assert write_response.status_code == 400
    read.assert_not_called()
    write.assert_not_called()


@pytest.mark.parametrize("scoped_server", [True], indirect=True)
@pytest.mark.asyncio
async def test_shared_status_refuses_missing_scope_before_reading_leak(scoped_server):
    client, storage = scoped_server
    with mock.patch.object(
        storage, "list_memories", wraps=storage.list_memories
    ) as read:
        response = await client.get("/status")

    assert response.status_code == 400
    read.assert_not_called()


@pytest.mark.parametrize("scoped_server", [False], indirect=True)
@pytest.mark.asyncio
async def test_non_shared_routes_keep_the_configured_repo_fallback(scoped_server):
    client, storage = scoped_server

    read_response = await client.get("/memories")
    write_response = await client.post("/memories", json={"content": "fallback write"})

    assert read_response.status_code == 200
    assert [item["content"] for item in read_response.json()] == ["must not leak"]
    assert write_response.status_code == 200
    assert write_response.json()["repo_id"] == "leak"
    assert {item["content"] for item in storage.list_memories(repo_id="leak")} == {
        "must not leak",
        "fallback write",
    }

