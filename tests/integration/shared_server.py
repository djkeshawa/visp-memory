import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from visp_memory import Memory, MemoryConfig
from visp_memory.core.intent_evaluator import IntentEvaluator
from visp_memory.core.lifecycle import MemoryLifecycleManager
from visp_memory.core.model_router import ModelRouter
from visp_memory.core.storage import LocalStorage
from visp_memory.server import app as server_app
from visp_memory.server.app import app
from visp_memory.server.auth_store import AuthStore


@pytest.fixture
def shared_server(monkeypatch, tmp_path: Path):
    data_dir = tmp_path / "shared-data"
    monkeypatch.setenv("VISP_MEMORY_SERVER_AUTH_ENABLED", "true")
    monkeypatch.setenv("VISP_MEMORY_SERVER_ALLOW_ANONYMOUS", "true")
    monkeypatch.setenv("VISP_MEMORY_SERVER_LOCAL_OWNER_MODE", "true")
    monkeypatch.setenv("VISP_MEMORY_SERVER_SHARED", "true")
    monkeypatch.setenv("VISP_MEMORY_STORAGE_DATA_DIR", str(data_dir))
    monkeypatch.setenv("VISP_MEMORY_EMBEDDING_PROVIDER", "noop")
    monkeypatch.delenv("VISP_MEMORY_REPO_ID", raising=False)

    state_names = (
        "storage",
        "storage_backend",
        "auth_store",
        "memory_lifecycle",
        "model_router",
        "intent_evaluator",
        "dreaming",
    )
    previous = {
        name: getattr(app.state, name)
        for name in state_names
        if hasattr(app.state, name)
    }

    storage = LocalStorage(data_dir)
    config = MemoryConfig()
    config.embedding.provider = "noop"
    config.storage.data_dir = data_dir
    config.server.auth_enabled = True
    config.server.allow_anonymous = True
    config.server.local_owner_mode = True
    config.server.shared = True
    config.server.host = "127.0.0.1"
    monkeypatch.setattr(server_app, "config", config)
    run_directory = tmp_path / "run"
    monkeypatch.setattr("visp_memory.server.owner_token.run_dir", lambda: run_directory)
    monkeypatch.setattr("visp_memory.core.remote.owner_auth.run_dir", lambda: run_directory)
    app.state.storage = storage
    app.state.storage_backend = "sqlite"
    app.state.auth_store = AuthStore(data_dir / "auth.db")
    app.state.memory_lifecycle = MemoryLifecycleManager(
        storage, data_dir / "lifecycle.db"
    )
    app.state.model_router = ModelRouter(config.llm)
    app.state.intent_evaluator = IntentEvaluator(
        storage, app.state.model_router, config.llm
    )

    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=0,
            log_level="warning",
        )
    )
    bound_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    bound_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    bound_socket.bind(("127.0.0.1", 0))
    bound_socket.listen()
    config.server.port = bound_socket.getsockname()[1]
    thread = threading.Thread(
        target=server.run,
        kwargs={"sockets": [bound_socket]},
        daemon=True,
    )
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=5)
        pytest.fail("The shared test server did not start")

    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        stopped = not thread.is_alive()
        if bound_socket.fileno() != -1:
            bound_socket.close()
        for name in state_names:
            if name in previous:
                setattr(app.state, name, previous[name])
            elif hasattr(app.state, name):
                delattr(app.state, name)
        if not stopped:
            pytest.fail("The shared test server did not stop")


@pytest.fixture
def client_memory(shared_server, tmp_path):
    memories = []

    def create(repo_id: str) -> Memory:
        config = MemoryConfig(repo_id=repo_id)
        config.storage.mode = "client"
        config.storage.server_url = shared_server
        config.storage.data_dir = tmp_path / "clients" / repo_id
        config.embedding.provider = "noop"
        memory = Memory(config=config)
        memories.append(memory)
        return memory

    yield create

    for memory in memories:
        memory.close()
