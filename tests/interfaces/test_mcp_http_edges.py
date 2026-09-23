"""Behavior-level coverage for the stateless MCP HTTP boundary.

These tests exercise the transport itself with small ASGI managers.  The
end-to-end tests cover successful MCP calls; this module pins the failure
contracts that are otherwise easy to miss because the MCP SDK owns the
response lifecycle.
"""

from contextlib import asynccontextmanager
from types import ModuleType, SimpleNamespace

import pytest

from visp_memory.interfaces.mcp import MCPRequestContext, bind_mcp_request_context
from visp_memory.interfaces.mcp_http import (
    StatelessMCPApp,
    _authorized,
    _bearer_token,
    _safe_error_payload,
    _sanitize_json_rpc_payload,
    _sanitize_mcp_body,
    _sanitize_sse_body,
    _send_json,
    _transport_error,
)


class _Manager:
    def __init__(self, action="ok"):
        self.action = action
        self.calls = 0
        self.lifecycle = []

    @asynccontextmanager
    async def run(self):
        self.lifecycle.append("enter")
        try:
            yield
        finally:
            self.lifecycle.append("exit")

    async def handle_request(self, scope, receive, send):
        self.calls += 1
        if self.action == "raise-before":
            raise RuntimeError("backend details must remain server-side")
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"x-request-id", b"stale-id"),
                ],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b'{"error":{"message":"database password"}}',
            }
        )
        if self.action == "raise-after":
            raise RuntimeError("response already started")


async def _call_app(app, scope):
    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    await app(scope, receive, send)
    return messages


def _http_scope(path="/mcp"):
    return {"type": "http", "path": path, "headers": []}


def test_transport_error_helpers_are_correlated_and_safe():
    request_id = "request-123"

    assert _safe_error_payload("request_failed", "safe", request_id) == {
        "success": False,
        "code": "request_failed",
        "request_id": request_id,
        "message": "safe",
    }
    assert _transport_error("not_found", "missing", request_id) == (
        b'{"success": false, "error": "not_found", "code": "not_found", '
        b'"request_id": "request-123", "message": "missing"}'
    )


@pytest.mark.parametrize(
    ("scope", "expected"),
    [
        ({"headers": []}, None),
        ({"headers": [(b"authorization", b"Basic abc")]}, None),
        ({"headers": [(b"authorization", b"Bearer ")]}, None),
        ({"headers": [(b"authorization", b"Bearer \xff")]}, None),
        ({"headers": [(b"authorization", b"Bearer llmm_valid")]}, "llmm_valid"),
    ],
)
def test_bearer_parser_rejects_malformed_credentials(scope, expected):
    assert _bearer_token(scope) == expected


def test_static_authorization_uses_the_exact_bearer_value():
    assert _authorized({"headers": [(b"authorization", b"Bearer expected")]}, "expected")
    assert not _authorized({"headers": [(b"authorization", b"Bearer other")]}, "expected")
    assert not _authorized({"headers": [(b"Authorization", b"Bearer expected")]}, "expected")


def test_sanitize_json_rpc_payload_handles_sdk_and_tool_error_shapes():
    request_id = "req-1"
    payload = {
        "error": {
            "code": -32603,
            "message": '{"success": false, "code": "bad_input", "message": "use repo_id"}',
        },
        "result": {
            "isError": True,
            "content": [
                {
                    "type": "text",
                    "text": (
                        '{"success": false, "code": "authorization_denied", '
                        '"message": "private"}'
                    ),
                },
                {"type": "text", "text": "validation failed: repo_id is required"},
                {"type": "text", "text": "provider secret must not escape"},
                {"type": "image", "data": "opaque"},
            ],
        },
    }

    result = _sanitize_json_rpc_payload(payload, request_id)

    assert result["error"]["message"] == "use repo_id"
    assert result["error"]["data"] == {
        "success": False,
        "code": "bad_input",
        "message": "use repo_id",
        "request_id": request_id,
    }
    content = result["result"]["content"]
    assert content[0]["text"] == (
        '{"success": false, "code": "authorization_denied", "message": "private", '
        '"request_id": "req-1"}'
    )
    assert '"code": "authorization_denied"' in content[1]["text"]
    assert '"code": "request_failed"' in content[2]["text"]
    assert content[3] == {"type": "image", "data": "opaque"}


def test_sanitize_json_rpc_payload_replaces_unstructured_protocol_errors():
    payload = {"error": {"code": -32603, "message": "SQL password=hidden"}}

    result = _sanitize_json_rpc_payload(payload, "req-2")

    assert result["error"]["message"] == "The MCP request failed; check server logs."
    assert result["error"]["data"] == _safe_error_payload(
        "request_failed", "The MCP request failed; check server logs.", "req-2"
    )
    assert "hidden" not in str(result)


def test_sanitize_mcp_body_preserves_non_json_and_malformed_payloads():
    body = b"not-json"
    assert _sanitize_mcp_body(body, "req-3", b"text/plain") == body
    assert _sanitize_mcp_body(body, "req-3", b"application/json") == body
    assert _sanitize_json_rpc_payload([], "req-3") == []


def test_sanitize_sse_body_preserves_comments_and_handles_newline_variants():
    body = (
        b": keep this comment\r\n"
        b"event: message\n"
        b"data: {\"error\": {\"message\": \"secret\"}}\r\n"
        b"data: not-json\r"
    )

    result = _sanitize_sse_body(body, "req-sse")
    text = result.decode()

    assert ": keep this comment\r\n" in text
    assert "event: message\n" in text
    assert text.count('"code": "request_failed"') == 2
    assert "secret" not in text
    assert "\r\ndata:" in text
    assert text.endswith("\r")


def test_sanitize_sse_body_returns_invalid_utf8_unchanged():
    body = b"data: \xff\n"
    assert _sanitize_sse_body(body, "req-sse") == body


@pytest.mark.asyncio
async def test_send_json_emits_content_type_and_optional_request_id():
    messages = []

    async def send(message):
        messages.append(message)

    await _send_json(send, 401, b"{}")
    assert messages[0]["status"] == 401
    assert messages[0]["headers"] == [(b"content-type", b"application/json")]
    assert messages[1] == {"type": "http.response.body", "body": b"{}"}

    messages.clear()
    await _send_json(send, 404, b"{}", request_id="req-4")
    assert (b"x-request-id", b"req-4") in messages[0]["headers"]


@pytest.mark.asyncio
async def test_http_transport_rejects_unknown_path_without_calling_manager():
    manager = _Manager()
    messages = await _call_app(StatelessMCPApp(manager), _http_scope("/other"))

    assert manager.calls == 0
    assert messages[0]["status"] == 404
    request_ids = [value for name, value in messages[0]["headers"] if name == b"x-request-id"]
    assert len(request_ids) == 1
    assert len(request_ids[0]) == 32
    assert b"not_found" in messages[1]["body"]


@pytest.mark.asyncio
async def test_http_transport_ignores_non_http_scopes():
    manager = _Manager()
    messages = await _call_app(StatelessMCPApp(manager), {"type": "websocket"})

    assert messages == []
    assert manager.calls == 0


@pytest.mark.asyncio
async def test_http_transport_returns_authentication_failure_without_leaking_exception(
    monkeypatch,
):
    manager = _Manager()
    app = StatelessMCPApp(manager, token="expected")

    def explode(scope):
        raise RuntimeError("database password=secret")

    monkeypatch.setattr(app, "_authenticate", explode)
    messages = await _call_app(app, _http_scope())

    assert messages[0]["status"] == 500
    assert b"authentication_failed" in messages[1]["body"]
    assert b"secret" not in messages[1]["body"]
    assert manager.calls == 0


@pytest.mark.asyncio
async def test_http_transport_returns_authentication_required_for_closed_config():
    manager = _Manager()
    config = SimpleNamespace(server=SimpleNamespace(auth_enabled=True))
    messages = await _call_app(StatelessMCPApp(manager, config=config), _http_scope())

    assert messages[0]["status"] == 401
    assert b"authentication_required" in messages[1]["body"]


@pytest.mark.asyncio
async def test_http_transport_reports_manager_failure_before_response():
    manager = _Manager("raise-before")
    messages = await _call_app(StatelessMCPApp(manager), _http_scope())

    assert messages[0]["status"] == 500
    assert b"request_failed" in messages[1]["body"]


@pytest.mark.asyncio
async def test_http_transport_does_not_append_second_error_after_response_started():
    manager = _Manager("raise-after")
    messages = await _call_app(StatelessMCPApp(manager), _http_scope())

    assert [message["type"] for message in messages] == [
        "http.response.start",
        "http.response.body",
    ]
    assert messages[0]["headers"].count((b"x-request-id", messages[0]["headers"][-1][1])) == 1
    assert b"database password" not in messages[1]["body"]
    assert b"request_failed" in messages[1]["body"]


@pytest.mark.asyncio
async def test_http_transport_rewrites_response_id_and_sanitizes_body():
    manager = _Manager()
    messages = await _call_app(StatelessMCPApp(manager), _http_scope())

    request_id = messages[0]["headers"][-1][1]
    assert messages[0]["headers"].count((b"x-request-id", request_id)) == 1
    assert b"stale-id" not in str(messages[0]["headers"]).encode()
    assert b"database password" not in messages[1]["body"]
    assert b"request_failed" in messages[1]["body"]


@pytest.mark.asyncio
async def test_lifespan_enters_and_exits_manager_once():
    manager = _Manager()
    app = StatelessMCPApp(manager)
    messages = []
    receives = iter(
        [{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}]
    )

    async def receive():
        return next(receives)

    async def send(message):
        messages.append(message)

    await app({"type": "lifespan"}, receive, send)

    assert messages == [
        {"type": "lifespan.startup.complete"},
        {"type": "lifespan.shutdown.complete"},
    ]
    assert manager.lifecycle == ["enter", "exit"]


def test_authenticate_resolves_pat_static_and_local_contexts():
    from visp_memory.interfaces.mcp_http import StatelessMCPApp

    principal = {
        "user_id": "user-1",
        "username": "alice",
        "scopes": ["memory:read"],
        "repo_ids": ["repo-a"],
    }
    pat_app = StatelessMCPApp(
        _Manager(),
        auth_store=SimpleNamespace(authenticate_token=lambda token: principal),
    )
    assert pat_app._authenticate({"headers": [(b"authorization", b"Bearer llmm_x")]})
    assert pat_app._authenticate({"headers": []}) is None
    invalid_pat_app = StatelessMCPApp(
        _Manager(), auth_store=SimpleNamespace(authenticate_token=lambda token: None)
    )
    assert invalid_pat_app._authenticate(
        {"headers": [(b"authorization", b"Bearer llmm_invalid")]}
    ) is None

    static_app = StatelessMCPApp(_Manager(), token="static")
    static_principal = static_app._authenticate(
        {"headers": [(b"authorization", b"Bearer static")]}
    )
    assert static_principal.auth_type == "legacy"
    assert static_app._authenticate({"headers": []}) is None

    local = StatelessMCPApp(_Manager())._authenticate({"headers": []})
    assert local.user_id == "local"
    assert local.is_admin is True


def test_authenticate_fails_closed_when_authentication_is_enabled_without_store():
    config = SimpleNamespace(server=SimpleNamespace(auth_enabled=True))
    assert StatelessMCPApp(_Manager(), config=config)._authenticate({"headers": []}) is None


def test_build_http_app_fails_clearly_when_mcp_is_unavailable(monkeypatch):
    import visp_memory.interfaces.mcp_http as mcp_http

    monkeypatch.setattr(mcp_http, "MCP_AVAILABLE", False)
    with pytest.raises(ImportError, match="MCP package not installed"):
        mcp_http.build_http_app(server=object())


def test_build_http_app_creates_pat_store_from_authenticated_server_config(tmp_path):
    from visp_memory import Memory, MemoryConfig
    from visp_memory.interfaces import mcp as mcp_module
    from visp_memory.interfaces.mcp import create_mcp_server
    from visp_memory.interfaces.mcp_http import build_http_app

    config = MemoryConfig(repo_id="repo-a")
    config.server.auth_enabled = True
    config.storage.data_dir = tmp_path
    config.embedding.provider = "noop"
    memory = Memory(config=config)
    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(mcp_module, "Memory", lambda *args, **kwargs: memory)
        server = create_mcp_server()

    app = build_http_app(server)
    assert app._auth_store is not None
    assert app._auth_store.path == tmp_path / "auth.db"


def test_build_http_app_can_construct_default_server(monkeypatch):
    import visp_memory.interfaces.mcp_http as mcp_http

    class Manager:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    server = object()
    monkeypatch.setattr(mcp_http, "create_mcp_server", lambda: server)
    monkeypatch.setattr(
        "mcp.server.streamable_http_manager.StreamableHTTPSessionManager", Manager
    )

    app = mcp_http.build_http_app()

    assert app._manager.kwargs["app"] is server
    assert app._manager.kwargs["stateless"] is True


def test_main_reports_missing_mcp_dependency(monkeypatch, capsys):
    import visp_memory.interfaces.mcp_http as mcp_http

    monkeypatch.setattr(mcp_http, "MCP_AVAILABLE", False)
    assert mcp_http.main() == 1
    assert "MCP package not installed" in capsys.readouterr().out


def test_main_starts_uvicorn_with_loopback_token_configuration(monkeypatch):
    import sys

    import visp_memory.config
    import visp_memory.interfaces.mcp_http as mcp_http

    uvicorn = ModuleType("uvicorn")
    calls = []
    uvicorn.run = lambda *args, **kwargs: calls.append((args, kwargs))
    monkeypatch.setitem(sys.modules, "uvicorn", uvicorn)
    monkeypatch.setattr(mcp_http, "MCP_AVAILABLE", True)
    monkeypatch.setenv("VISP_MEMORY_MCP_HTTP_HOST", "127.0.0.1")
    monkeypatch.setenv("VISP_MEMORY_MCP_HTTP_PORT", "9988")
    monkeypatch.setenv("VISP_MEMORY_MCP_HTTP_TOKEN", "token")
    config = SimpleNamespace(server=SimpleNamespace(auth_enabled=False))
    monkeypatch.setattr(visp_memory.config, "load_config", lambda: config)
    built = object()
    monkeypatch.setattr(mcp_http, "build_http_app", lambda **kwargs: built)

    assert mcp_http.main() == 0
    assert calls == [((built,), {"host": "127.0.0.1", "port": 9988, "log_level": "info"})]


def test_nested_mcp_contexts_restore_the_previous_context():
    from visp_memory.interfaces.mcp import current_mcp_request_context

    outer = MCPRequestContext(transport="http", request_id="outer")
    inner = MCPRequestContext(transport="stdio", request_id="inner")
    baseline = current_mcp_request_context()
    with bind_mcp_request_context(outer):
        assert current_mcp_request_context() == outer
        with bind_mcp_request_context(inner):
            assert current_mcp_request_context() == inner
        assert current_mcp_request_context() == outer
    assert current_mcp_request_context() == baseline
