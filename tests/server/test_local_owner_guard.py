"""A local-owner server must not be reachable through a name the attacker picks.

DNS rebinding points a hostile page's own hostname at 127.0.0.1, so the browser
sends the request from a loopback peer with the page's origin and Host. The peer
check alone cannot tell that from the owner's dashboard; Host and Origin can.
"""

import tempfile
import unittest.mock as mock
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from visp_memory.config import MemoryConfig, ServerConfig
from visp_memory.core.lifecycle import MemoryLifecycleManager
from visp_memory.core.storage import LocalStorage
from visp_memory.server.app import app
from visp_memory.server.auth_store import AuthStore

LOOPBACK_PEER = ("127.0.0.1", 51515)
REMOTE_PEER = ("203.0.113.5", 51515)
REPO_ID = "guarded-project"
EVIL = "https://evil.example"
OWN_HOST = "127.0.0.1:8000"


@pytest_asyncio.fixture
async def served(request):
    """Serve the app in the given local-owner mode to the given peer."""
    peer, local_owner_mode = getattr(request, "param", (LOOPBACK_PEER, True))
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        data_dir = Path(tmpdir)
        storage = LocalStorage(data_dir)
        storage.store_memory("Only the owner may read this", repo_id=REPO_ID)
        app.state.storage = storage
        app.state.auth_store = AuthStore(data_dir / "auth.db")
        app.state.memory_lifecycle = MemoryLifecycleManager(storage, data_dir / "lifecycle.db")
        config = MemoryConfig()
        config.embedding.provider = "noop"
        config.server = ServerConfig(
            auth_enabled=True, allow_anonymous=True, local_owner_mode=local_owner_mode
        )
        transport = httpx.ASGITransport(app=app, client=peer)
        with (
            mock.patch("visp_memory.server.auth.load_config", return_value=config),
            mock.patch("visp_memory.server.routers.memories.load_config", return_value=config),
        ):
            # Each test names the Host it sends, so the base URL host is a dummy.
            async with httpx.AsyncClient(transport=transport, base_url="http://x") as client:
                yield client
        storage.close()


def read(client, host):
    return client.get(f"/memories?repo_id={REPO_ID}", headers={"Host": host})


def write(client, headers, host=OWN_HOST):
    return client.post(
        "/memories",
        json={"content": "Written by a page", "repo_id": REPO_ID},
        headers={"Host": host, **headers},
    )


def stored_contents():
    return {row["content"] for row in app.state.storage.list_memories(repo_id=REPO_ID)}


@pytest.mark.asyncio
@pytest.mark.parametrize("host", ["evil.example:8000", "evil.example", "127.0.0.1.evil.example"])
async def test_a_rebound_host_cannot_reach_the_local_owner_view(served, host):
    response = await read(served, host)
    assert response.status_code == 421
    assert "Only the owner" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "localhost:8000",
        "LOCALHOST:8000",
        "127.0.0.1",
        "127.0.0.1:8765",
        "[::1]",
        "[::1]:8000",
    ],
)
async def test_loopback_host_names_keep_working(served, host):
    response = await read(served, host)
    assert response.status_code == 200
    assert [row["content"] for row in response.json()] == ["Only the owner may read this"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1@evil.example",
        "localhost:80@evil.example",
        "localhost:8000, evil.example",
        "localhost.evil.example",
    ],
)
async def test_lookalike_hosts_are_rejected(served, host):
    assert (await read(served, host)).status_code == 421


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
async def test_cross_origin_unsafe_requests_are_rejected(served, method):
    response = await served.request(
        method,
        "/memories",
        json={"content": "Written by a page", "repo_id": REPO_ID},
        headers={"Host": OWN_HOST, "Origin": EVIL},
    )
    assert response.status_code == 403
    assert "Written by a page" not in stored_contents()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "origin",
    [
        "null",
        "http://localhost:9999",  # loopback, but neither allowlisted nor this server
        "http://evil.example:8000",
        "http://127.0.0.1:8000.evil.example",
    ],
)
async def test_only_allowlisted_or_same_origin_loopback_pages_may_write(served, origin):
    assert (await write(served, {"Origin": origin})).status_code == 403
    assert "Written by a page" not in stored_contents()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "origin,host",
    [
        ("http://127.0.0.1:8000", "127.0.0.1:8000"),  # dashboard, same origin
        ("http://localhost:8765", "localhost:8765"),  # dashboard on another port
        ("http://localhost:3000", OWN_HOST),  # allowlisted dev dashboard
        ("http://[::1]:8000", "[::1]:8000"),
    ],
)
async def test_the_dashboard_may_write(served, origin, host):
    response = await write(served, {"Origin": origin}, host=host)
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_requests_without_an_origin_still_write(served):
    # CLI, RemoteStorage and MCP clients are not browsers and send no Origin.
    assert (await write(served, {})).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("served", [(LOOPBACK_PEER, False)], indirect=True)
async def test_a_server_that_is_not_in_local_owner_mode_is_unaffected(served):
    assert (await read(served, "evil.example:8000")).status_code == 200
    assert (await write(served, {"Origin": EVIL})).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("served", [(REMOTE_PEER, True)], indirect=True)
async def test_a_remote_peer_is_not_covered_because_it_never_had_the_owner_view(served):
    # A hand-set mode on a public bind grants a remote caller nothing (see
    # test_local_owner_is_local); its Host is whatever its reverse proxy sends.
    assert (await read(served, "team.example")).status_code == 200
