import tempfile
import unittest.mock as mock
from pathlib import Path

import httpx
import pytest_asyncio

from llm_memory.config import MemoryConfig, ServerConfig
from llm_memory.core.intent_evaluator import IntentEvaluator
from llm_memory.core.lifecycle import MemoryLifecycleManager
from llm_memory.core.model_router import ModelRouter
from llm_memory.core.storage import LocalStorage
from llm_memory.server.app import app
from llm_memory.server.auth_store import AuthStore


@pytest_asyncio.fixture
async def client():
    # ignore_cleanup_errors: on Windows the SQLite WAL sidecar files can briefly
    # hold a handle on memories.db when the temp dir is torn down, which would
    # otherwise raise a spurious PermissionError after the test has passed.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        app.state.storage = LocalStorage(Path(tmpdir))
        app.state.auth_store = AuthStore(Path(tmpdir) / "auth.db")
        app.state.memory_lifecycle = MemoryLifecycleManager(
            app.state.storage, Path(tmpdir) / "lifecycle.db"
        )

        config = MemoryConfig()
        config.embedding.provider = "noop"
        config.server = ServerConfig(
            api_keys=["test_key"],
            auth_enabled=True,
            allow_anonymous=False,
        )
        config.storage.api_key = "test_key"
        app.state.model_router = ModelRouter(config.llm)
        app.state.intent_evaluator = IntentEvaluator(
            app.state.storage, app.state.model_router, config.llm
        )

        transport = httpx.ASGITransport(app=app)
        with mock.patch("llm_memory.server.auth.load_config", return_value=config):
            with mock.patch("llm_memory.server.routers.memories.load_config", return_value=config):
                with mock.patch(
                    "llm_memory.server.routers.intents.load_config",
                    return_value=config,
                ):
                    with mock.patch(
                        "llm_memory.server.routers.quality.load_config",
                        return_value=config,
                    ):
                        async with httpx.AsyncClient(
                            transport=transport,
                            base_url="http://testserver",
                        ) as test_client:
                            yield test_client
