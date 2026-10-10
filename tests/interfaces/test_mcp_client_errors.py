"""A client-mode MCP server says why the memory server failed, as an error.

Before this, a stopped server and a 400 both came back as a successful tool
result reading "request_failed ... check server logs" — the one place the agent
cannot look — while a full traceback went to stderr on every call.
"""

import json
import logging
import socket

import pytest

from tests.mcp_client import call_tool
from visp_memory import Memory, MemoryConfig
from visp_memory.interfaces import mcp as mcp_module

pytest_plugins = ["tests.integration.shared_server"]


def _closed_port_url() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return f"http://127.0.0.1:{port}"


async def _call(monkeypatch, memory, tool, arguments=None):
    monkeypatch.setenv("VISP_MEMORY_MCP_PROFILE", "full")
    monkeypatch.setattr(mcp_module, "Memory", lambda *a, **k: memory)
    server = mcp_module.create_mcp_server()
    return await call_tool(server, tool, arguments)


def _mcp_records(caplog):
    return [r for r in caplog.records if r.name == mcp_module.logger.name]


@pytest.fixture
def offline_memory(tmp_path):
    config = MemoryConfig(repo_id="proj-a")
    config.storage.mode = "client"
    config.storage.server_url = _closed_port_url()
    config.storage.data_dir = tmp_path / "client"
    config.embedding.provider = "noop"
    memory = Memory(config=config)
    yield memory
    memory.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["memory_list_intents", "memory_stats"])
async def test_stopped_server_is_reported_as_unavailable_with_url_and_hint(
    offline_memory, monkeypatch, caplog, tool
):
    url = offline_memory.config.storage.server_url

    with caplog.at_level(logging.WARNING):
        result = await _call(monkeypatch, offline_memory, tool)

    payload = json.loads(result.content[0].text)
    assert result.is_error is True
    assert payload["success"] is False
    assert payload["code"] == "server_unavailable"
    assert payload["server_url"] == url
    assert url in payload["message"]
    assert "visp-memory serve --shared" in payload["message"]
    records = _mcp_records(caplog)
    assert len(records) == 1
    assert records[0].exc_info is None


@pytest.mark.asyncio
async def test_server_refusal_is_reported_with_the_server_detail(
    client_memory, monkeypatch, caplog
):
    memory = client_memory("proj-a")
    # A client with no project scope sends an unscoped read, which a shared
    # server refuses with 400 — the refusal this path must surface verbatim.
    memory._storage.repo_id = None

    with caplog.at_level(logging.WARNING):
        result = await _call(monkeypatch, memory, "memory_list_intents")

    payload = json.loads(result.content[0].text)
    assert result.is_error is True
    assert payload["code"] == "server_rejected"
    assert payload["status_code"] == 400
    assert payload["detail"] == "repo_id is required"
    assert "repo_id is required" in payload["message"]
    records = _mcp_records(caplog)
    assert len(records) == 1
    assert records[0].exc_info is None


@pytest.mark.asyncio
async def test_unexpected_failure_keeps_the_generic_path(offline_memory, monkeypatch, caplog):
    async def explode(*args, **kwargs):
        raise RuntimeError("database password = super-secret")

    monkeypatch.setattr(mcp_module, "handle_tool", explode)

    with caplog.at_level(logging.WARNING):
        result = await _call(monkeypatch, offline_memory, "memory_list_intents")

    text = result.content[0].text
    assert "super-secret" not in text
    assert result.is_error is False
    assert json.loads(text)["code"] == "request_failed"
    assert _mcp_records(caplog)[-1].exc_info is not None
