from unittest.mock import patch

import httpx
import pytest

from visp_memory.config import MemoryConfig, ServerConfig
from visp_memory.server.app import app


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "peer,expected", [("127.0.0.1", {"first", "second"}), ("203.0.113.5", set())]
)
async def test_project_discovery_obeys_local_owner_boundary(client, peer, expected):
    for repo_id in ("first", "second", "archived"):
        app.state.storage.store_memory(repo_id, repo_id=repo_id, auto_link=False)
    app.state.storage.update_repository("archived", status="archived")
    app.state.storage.store_memory("unattributed memory", auto_link=False)
    config = MemoryConfig()
    config.server = ServerConfig(
        auth_enabled=True,
        allow_anonymous=True,
        local_owner_mode=True,
    )
    transport = httpx.ASGITransport(app=app, client=(peer, 12345))
    with patch("visp_memory.server.auth.load_config", return_value=config):
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as browser:
            scopes = await browser.get("/repos/scopes")
            assert scopes.status_code == 200
            assert {row["id"] for row in scopes.json()} == expected
            repositories = await browser.get("/repos")
            assert {row["id"] for row in repositories.json()} == expected
            detail = await browser.get("/repos/second")
            assert detail.status_code == (200 if expected else 404)
            archive = await browser.post("/repos/second/archive")
            assert archive.status_code == 403
