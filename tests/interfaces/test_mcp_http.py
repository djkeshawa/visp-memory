"""The stateless HTTP transport serves self-contained MCP requests.

What these tests pin, in order of what would hurt if it silently broke:

- A tool call over plain HTTP works end to end with no session of any kind:
  no initialize handshake first, no session id issued or demanded. That IS
  the statelessness claim — each request must stand alone, or a load balancer
  cannot send it to any replica.
- The bearer-token gate rejects before the MCP layer is reached, because the
  transport exposes the write surface.
- ``memory_model_task`` cannot hang waiting for sampling a stateless request
  can never deliver: with no client sampling capability it must take the
  server-side provider path and refuse with the repair when none is configured.
- ``main`` refuses to bind a non-loopback address without a token.
"""

import json
from unittest import mock

import httpx
import pytest

from visp_memory import Memory, MemoryConfig

pytest.importorskip("mcp")

from visp_memory.interfaces.mcp import create_mcp_server  # noqa: E402
from visp_memory.interfaces.mcp_http import build_http_app, main  # noqa: E402

HEADERS = {
    "content-type": "application/json",
    "accept": "application/json, text/event-stream",
}


def _scoped_memory(tmp_path):
    # An MCP server runs against an initialized project, which always has a
    # repository scope; without one every write tool is correctly refused.
    config = MemoryConfig(repo_id="http-repo")
    config.storage.data_dir = tmp_path
    config.embedding.provider = "noop"
    return Memory(config=config)


def _build_app(tmp_path, **kwargs):
    with mock.patch(
        "visp_memory.interfaces.mcp.Memory", return_value=_scoped_memory(tmp_path)
    ):
        server = create_mcp_server()
    return build_http_app(server, **kwargs)


def _rpc(method, params=None, request_id=1):
    body = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        body["params"] = params
    return body


def _tool_call(name, arguments):
    return _rpc("tools/call", {"name": name, "arguments": arguments})


def _result_text(response):
    payload = json.loads(response.text)
    assert "error" not in payload, payload
    return payload["result"]["content"][0]["text"]


class _Client:
    """An HTTP client against the app, with its lifespan (task group) running."""

    def __init__(self, app):
        self._app = app

    async def __aenter__(self):
        self._lifespan = self._app.lifespan()
        await self._lifespan.__aenter__()
        self._client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self._app), base_url="http://testserver"
        )
        return self._client

    async def __aexit__(self, *exc):
        await self._client.aclose()
        await self._lifespan.__aexit__(*exc)


class TestStatelessTransport:
    @pytest.mark.asyncio
    async def test_tool_call_needs_no_handshake_and_no_session(self, tmp_path):
        app = _build_app(tmp_path)
        async with _Client(app) as client:
            response = await client.post(
                "/mcp",
                json=_tool_call("memory_record", {"event": "written over stateless http"}),
                headers=HEADERS,
            )
        assert response.status_code == 200
        assert "mcp-session-id" not in response.headers
        assert "Recorded event" in _result_text(response)

    @pytest.mark.asyncio
    async def test_consecutive_requests_share_nothing_but_the_store(self, tmp_path):
        app = _build_app(tmp_path)
        async with _Client(app) as client:
            first = await client.post(
                "/mcp",
                json=_tool_call("memory_record", {"event": "first request wrote this"}),
                headers=HEADERS,
            )
            second = await client.post(
                "/mcp",
                json=_tool_call("memory_recall", {"query": "first request wrote"}),
                headers=HEADERS,
            )
        assert first.status_code == 200
        # The second request carried no session identity, yet reads what the
        # first durably wrote: state lives in storage, not in the transport.
        assert "first request wrote this" in _result_text(second)

    @pytest.mark.asyncio
    async def test_bearer_token_gates_every_request(self, tmp_path):
        app = _build_app(tmp_path, token="s3cret")
        async with _Client(app) as client:
            unauthenticated = await client.post(
                "/mcp", json=_rpc("tools/list"), headers=HEADERS
            )
            wrong = await client.post(
                "/mcp",
                json=_rpc("tools/list"),
                headers={**HEADERS, "authorization": "Bearer wrong"},
            )
            right = await client.post(
                "/mcp",
                json=_rpc("tools/list"),
                headers={**HEADERS, "authorization": "Bearer s3cret"},
            )
        assert unauthenticated.status_code == 401
        assert wrong.status_code == 401
        assert right.status_code == 200

    @pytest.mark.asyncio
    async def test_model_task_takes_provider_fallback_not_sampling(
        self, tmp_path, monkeypatch
    ):
        # memory_model_task is outside the core profile; the full profile
        # exposes it. A stateless request carries no client capabilities, so
        # the sampling branch must be skipped and the unconfigured server-side
        # provider must refuse with the repair — not hang, not crash.
        monkeypatch.setenv("VISP_MEMORY_MCP_PROFILE", "full")
        app = _build_app(tmp_path)
        async with _Client(app) as client:
            response = await client.post(
                "/mcp",
                json=_tool_call("memory_model_task", {"task": "answer", "prompt": "hi"}),
                headers=HEADERS,
            )
        assert response.status_code == 200
        text = _result_text(response)
        assert "mcp-sampling" not in text
        assert "No server-side LLM provider is configured" in text


class TestMainGuards:
    def test_refuses_non_loopback_bind_without_token(self, monkeypatch, capsys):
        monkeypatch.setenv("VISP_MEMORY_MCP_HTTP_HOST", "0.0.0.0")
        monkeypatch.delenv("VISP_MEMORY_MCP_HTTP_TOKEN", raising=False)
        assert main() == 1
        out = capsys.readouterr().out
        assert "VISP_MEMORY_MCP_HTTP_TOKEN" in out
