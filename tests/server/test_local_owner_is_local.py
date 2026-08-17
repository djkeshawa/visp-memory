"""Open local mode grants nothing over a network interface.

`allow_anonymous` is settable from the config file and the environment, not only by
the loopback path in `_ensure_serveable_auth_config`, and that function returns
before its non-loopback refusal when the flag is already set. So an operator can
reach an anonymous, teamless principal on a `0.0.0.0` bind.

That principal must read what it read before this branch: nothing. The local-owner
grant needs the explicit mode flag *and* a request that came from this machine.
"""

import tempfile
import unittest.mock as mock
from pathlib import Path
from types import SimpleNamespace

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
from visp_memory.server.auth import UserContext, is_local_owner_request
from visp_memory.server.auth_store import AuthStore

REPO_ID = "reachable-project"
REMOTE_PEER = ("203.0.113.5", 51515)
LOOPBACK_PEER = ("127.0.0.1", 51515)


@pytest.fixture(autouse=True)
def _no_ambient_server_env(monkeypatch):
    """These tests decide the mode themselves.

    `ServerConfig` reads the environment, and `_ensure_serveable_auth_config`
    writes to it — it has to, because the uvicorn reload subprocess inherits it.
    A CLI test that exercises that function therefore leaks the mode into this
    process, and "the flag is off" is the thing half of these tests assert.
    """
    for name in (
        "VISP_MEMORY_SERVER_LOCAL_OWNER_MODE",
        "VISP_MEMORY_SERVER_ALLOW_ANONYMOUS",
        "VISP_MEMORY_SERVER_DEFAULT_TEAM",
    ):
        monkeypatch.delenv(name, raising=False)


def _config(repo_id: str | None = REPO_ID, **server_overrides) -> MemoryConfig:
    config = MemoryConfig(repo_id=repo_id)
    config.embedding.provider = "noop"
    config.server = ServerConfig(
        auth_enabled=True, allow_anonymous=True, **server_overrides
    )
    return config


class _Request:
    """The one thing is_local_owner_request reads off a request: its peer."""

    def __init__(self, host):
        self.client = None if host is None else SimpleNamespace(host=host)

    @classmethod
    def from_host(cls, host):
        return cls(host)


@pytest.mark.parametrize(
    "host, expected",
    [
        ("127.0.0.1", True),
        ("::1", True),
        ("::ffff:127.0.0.1", True),
        ("203.0.113.5", False),
        ("10.0.0.4", False),
        (None, False),
    ],
)
def test_only_a_request_from_this_machine_is_the_local_owner(host, expected):
    assert (
        is_local_owner_request(_Request.from_host(host), _config(local_owner_mode=True))
        is expected
    )


def test_the_mode_flag_alone_is_not_enough():
    remote = _Request.from_host("203.0.113.5")

    assert is_local_owner_request(remote, _config(local_owner_mode=True)) is False


def test_loopback_alone_is_not_enough():
    # allow_anonymous on its own never grants the local view, however it was set.
    assert is_local_owner_request(_Request.from_host("127.0.0.1"), _config()) is False


@pytest_asyncio.fixture
async def served(request):
    """Serve a populated store to a peer address the test chooses."""
    peer, server_overrides = request.param
    repo_id = server_overrides.pop("_repo_id", REPO_ID)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        data_dir = Path(tmpdir)
        storage = LocalStorage(data_dir)
        storage.store_memory("A memory nobody outside this machine may read", repo_id=REPO_ID)

        app.state.storage = storage
        app.state.auth_store = AuthStore(data_dir / "auth.db")
        app.state.memory_lifecycle = MemoryLifecycleManager(storage, data_dir / "lifecycle.db")
        config = _config(repo_id=repo_id, **server_overrides)
        app.state.model_router = ModelRouter(config.llm)
        app.state.intent_evaluator = IntentEvaluator(storage, app.state.model_router, config.llm)

        transport = httpx.ASGITransport(app=app, client=peer)
        with (
            mock.patch("visp_memory.server.auth.load_config", return_value=config),
            mock.patch("visp_memory.server.routers.memories.load_config", return_value=config),
            mock.patch("visp_memory.server.app.config", config),
        ):
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                yield client


@pytest.mark.parametrize(
    "served", [(REMOTE_PEER, {"local_owner_mode": True})], indirect=True
)
@pytest.mark.asyncio
async def test_a_remote_caller_reads_nothing_even_with_the_mode_set(served):
    response = await served.get(f"/memories?repo_id={REPO_ID}")

    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.parametrize(
    "served", [(REMOTE_PEER, {"local_owner_mode": True})], indirect=True
)
@pytest.mark.asyncio
async def test_a_remote_caller_counts_nothing_even_with_the_mode_set(served):
    body = (await served.get(f"/status?repo_id={REPO_ID}")).json()

    assert body["stats"]["total_memories"] == 0


@pytest.mark.parametrize(
    "served", [(LOOPBACK_PEER, {"local_owner_mode": True})], indirect=True
)
@pytest.mark.asyncio
async def test_the_same_request_from_this_machine_reads_the_store(served):
    response = await served.get(f"/memories?repo_id={REPO_ID}")

    assert [memory["content"] for memory in response.json()] == [
        "A memory nobody outside this machine may read"
    ]


@pytest.mark.parametrize("served", [(LOOPBACK_PEER, {})], indirect=True)
@pytest.mark.asyncio
async def test_loopback_without_the_mode_reads_nothing(served):
    response = await served.get(f"/memories?repo_id={REPO_ID}")

    assert response.json() == []


@pytest.mark.parametrize(
    "served", [(LOOPBACK_PEER, {"local_owner_mode": True})], indirect=True
)
@pytest.mark.asyncio
async def test_the_local_owner_is_still_refused_every_admin_surface(served):
    refusals = {
        "/platform/audit-log": await served.get("/platform/audit-log"),
        "/maintenance/verify": await served.get("/maintenance/verify"),
        "/auth/users": await served.get("/auth/users"),
    }

    assert {path: r.status_code for path, r in refusals.items()} == {
        "/platform/audit-log": 403,
        "/maintenance/verify": 403,
        "/auth/users": 403,
    }


def test_the_local_owner_context_is_not_an_admin_context():
    # Belt and braces on the claim the whole grant rests on.
    owner = UserContext(user_id="anonymous", username="anonymous", is_local_owner=True)

    assert owner.is_admin is False


@pytest.mark.parametrize(
    "served",
    [(LOOPBACK_PEER, {"local_owner_mode": True, "_repo_id": None})],
    indirect=True,
)
@pytest.mark.asyncio
async def test_a_store_with_no_configured_project_still_reports_its_stats(served):
    # The dashboard's getStats() takes an optional repo, so a store that was never
    # given a repo_id and has no project selected asks for exactly this. It was a
    # 400 before this branch; answering 403 instead would put LC-84's blank cards
    # straight back on the page.
    response = await served.get("/status")

    assert response.status_code == 200
    assert response.json()["stats"]["total_memories"] == 1


@pytest.mark.parametrize(
    "served", [(REMOTE_PEER, {"local_owner_mode": True, "_repo_id": None})], indirect=True
)
@pytest.mark.asyncio
async def test_an_unscoped_status_reports_nothing_to_a_caller_entitled_to_nothing(served):
    response = await served.get("/status")

    assert response.status_code == 200
    assert response.json()["stats"]["total_memories"] == 0


@pytest.mark.parametrize(
    "served", [(REMOTE_PEER, {"local_owner_mode": True})], indirect=True
)
@pytest.mark.asyncio
async def test_a_scan_that_filled_its_bound_says_so(served, monkeypatch):
    # The filtered path counts rows it has read, so it is bounded. A bound that is
    # reached and not reported is a confidently wrong number, which is the shape of
    # the defect this whole change exists to remove.
    monkeypatch.setattr(server_app, "SCOPED_STATS_SCAN_LIMIT", 1)

    body = (await served.get(f"/status?repo_id={REPO_ID}")).json()

    assert body["stats"]["truncated"] is True
    assert body["stats_status"] == "partial"
