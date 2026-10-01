import json

import pytest
from pydantic import ValidationError

from tests.server.test_owner_maintenance import owner_client as owner_client
from tests.server.test_portability import OWNER_HEADERS, graph_document
from visp_memory.config import MemoryConfig, ServerConfig
from visp_memory.server import app as server_app


def test_import_body_limit_defaults_and_env_override(monkeypatch):
    monkeypatch.delenv("VISP_MEMORY_SERVER_MAX_IMPORT_BODY_BYTES", raising=False)
    assert ServerConfig().max_import_body_bytes == 64 * 1024 * 1024
    config = MemoryConfig()
    monkeypatch.setenv("VISP_MEMORY_SERVER_MAX_IMPORT_BODY_BYTES", "1234")
    config.apply_env_overrides()
    assert config.server.max_import_body_bytes == 1234
    assert ServerConfig().max_import_body_bytes == 1234
    with pytest.raises(ValidationError):
        ServerConfig(max_import_body_bytes=0)


@pytest.mark.asyncio
@pytest.mark.parametrize("streamed", [False, True])
@pytest.mark.parametrize("path", ["/repos/org%2Fproject/import", "/repos//import"])
async def test_import_rejects_oversized_invalid_json_before_parsing(owner_client, streamed, path):
    server_app.config.server.max_import_body_bytes = 16
    body = b"not json, and larger than the limit"

    async def chunks():
        yield body[:16]
        yield body[16:]

    response = await owner_client.post(
        path, content=chunks() if streamed else body,
        headers={**OWNER_HEADERS, "Content-Type": "application/json"},
    )
    assert response.status_code == 413, response.text
    assert server_app.app.state.storage.peek_memory("portable-memory") is None


@pytest.mark.asyncio
async def test_import_accepts_body_at_configured_limit(owner_client):
    body = json.dumps(graph_document()).encode()
    server_app.config.server.max_import_body_bytes = len(body)
    response = await owner_client.post(
        "/repos/org%2Fproject/import", content=body,
        headers={**OWNER_HEADERS, "Content-Type": "application/json"},
    )
    assert response.status_code == 200, response.text
    assert server_app.app.state.storage.peek_memory("portable-memory")["repo_id"] == "org/project"


@pytest.mark.asyncio
@pytest.mark.parametrize("declared_size", [b"999", b"1", b"invalid"])
async def test_import_body_limit_checks_headers_and_actual_bytes_under_root_path(declared_size):
    from visp_memory.server.import_body_limit import ImportBodyLimitMiddleware

    reads = []
    messages = []
    requests = iter([
        {"type": "http.request", "body": b"1234", "more_body": True},
        {"type": "http.request", "body": b"5", "more_body": False},
    ])

    async def receive():
        reads.append(True)
        return next(requests)

    async def parse_app(scope, receive, send):
        pytest.fail("oversized import must be rejected before the parser is invoked")

    async def send(message):
        messages.append(message)

    app = ImportBodyLimitMiddleware(parse_app, max_body_bytes=lambda: 4)
    scope = {
        "type": "http", "method": "POST", "path": "/proxy/repos/org/project/import",
        "root_path": "/proxy", "headers": [(b"content-length", declared_size)],
    }
    await app(scope, receive, send)
    assert messages[0]["status"] == 413
    assert len(reads) == (0 if declared_size == b"999" else 2)
