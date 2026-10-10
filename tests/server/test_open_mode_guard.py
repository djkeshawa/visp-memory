"""With auth disabled every request is the local administrator, so the Host and
Origin a browser sends are the only thing standing between a hostile page and the
write surface. Before this guard covered open mode, a page that rebound its own
name to 127.0.0.1 could read and write the whole store through REST and MCP-HTTP.
"""

import json
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

REPO_ID = "open-project"
EVIL = "http://evil.example"


@pytest_asyncio.fixture
async def open_server(request):
    """Serve the app with auth as given and the given extra allowed hosts."""
    auth_enabled, allowed_hosts = getattr(request, "param", (False, []))
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        data_dir = Path(tmpdir)
        storage = LocalStorage(data_dir)
        storage.store_memory("Private to this machine", repo_id=REPO_ID)
        app.state.storage = storage
        app.state.auth_store = AuthStore(data_dir / "auth.db")
        app.state.memory_lifecycle = MemoryLifecycleManager(storage, data_dir / "lifecycle.db")
        config = MemoryConfig()
        config.embedding.provider = "noop"
        config.server = ServerConfig(auth_enabled=auth_enabled, allowed_hosts=allowed_hosts)
        transport = httpx.ASGITransport(app=app, client=("203.0.113.5", 5000))
        with (
            mock.patch("visp_memory.server.auth.load_config", return_value=config),
            mock.patch("visp_memory.server.routers.memories.load_config", return_value=config),
        ):
            async with httpx.AsyncClient(transport=transport, base_url="http://x") as client:
                yield client
        storage.close()


def _read(client, host):
    return client.get(f"/memories?repo_id={REPO_ID}", headers={"Host": host})


def _write(client, host, origin):
    return client.post(
        "/memories",
        json={"content": "Written by a page", "repo_id": REPO_ID},
        headers={"Host": host, "Origin": origin},
    )


@pytest.mark.asyncio
async def test_a_rebound_host_cannot_reach_an_open_server(open_server):
    response = await _read(open_server, "evil.example:8000")

    assert response.status_code == 421
    assert "Private" not in response.text


@pytest.mark.asyncio
async def test_loopback_hosts_still_reach_an_open_server(open_server):
    assert (await _read(open_server, "localhost:8000")).status_code == 200


@pytest.mark.asyncio
async def test_a_cross_origin_write_to_an_open_server_is_refused(open_server):
    assert (await _write(open_server, "127.0.0.1:8000", EVIL)).status_code == 403
    assert (await _write(open_server, "127.0.0.1:8000", "http://127.0.0.1:8000")).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("open_server", [(False, ["memory.internal"])], indirect=True)
async def test_an_allowed_host_reaches_an_open_server(open_server):
    assert (await _read(open_server, "memory.internal:8000")).status_code == 200
    own_origin = await _write(open_server, "memory.internal:8000", "http://memory.internal:8000")
    assert own_origin.status_code == 200
    assert (await _read(open_server, "evil.example")).status_code == 421


@pytest.mark.asyncio
@pytest.mark.parametrize("open_server", [(False, ["*"])], indirect=True)
async def test_a_wildcard_turns_the_host_check_off(open_server):
    assert (await _read(open_server, "anything.example")).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("open_server", [(True, [])], indirect=True)
async def test_an_authenticated_server_is_not_host_checked(open_server):
    # It needs a credential, which is not what a rebound page has.
    assert (await _read(open_server, "evil.example")).status_code == 401


def test_allowed_hosts_parse_from_the_environment(monkeypatch):
    monkeypatch.setenv("VISP_MEMORY_SERVER_ALLOWED_HOSTS", "memory.internal, 10.0.0.5:8000")

    config = MemoryConfig()
    config.apply_env_overrides()

    assert config.server.allowed_hosts == ["memory.internal", "10.0.0.5:8000"]


# --- MCP over HTTP --------------------------------------------------------------


def _mcp_post(mcp_app, headers):
    async def post():
        transport = httpx.ASGITransport(app=mcp_app)
        async with mcp_app.lifespan():
            async with httpx.AsyncClient(transport=transport, base_url="http://x") as client:
                return await client.post(
                    "/mcp",
                    content=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
                    headers={
                        "content-type": "application/json",
                        "accept": "application/json, text/event-stream",
                        **headers,
                    },
                )

    return post()


@pytest.fixture
def local_mcp_app(tmp_path):
    pytest.importorskip("mcp")
    from tests.interfaces.test_mcp_http import _build_app

    return _build_app


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers,expected",
    [
        ({"Host": "evil.example:8765"}, 421),
        ({"Host": "127.0.0.1:8765", "Origin": EVIL}, 403),
        ({"Host": "127.0.0.1:8765"}, 200),
        ({"Host": "localhost:8765", "Origin": "http://localhost:3000"}, 200),
    ],
)
async def test_an_open_mcp_endpoint_refuses_rebound_requests(
    tmp_path, local_mcp_app, headers, expected
):
    response = await _mcp_post(local_mcp_app(tmp_path), headers)

    assert response.status_code == expected, response.text


@pytest.mark.asyncio
async def test_a_token_protected_mcp_endpoint_is_not_host_checked(tmp_path, local_mcp_app):
    mcp_app = local_mcp_app(tmp_path, token="shared-secret")

    response = await _mcp_post(
        mcp_app, {"Host": "evil.example", "authorization": "Bearer shared-secret"}
    )

    assert response.status_code == 200, response.text
