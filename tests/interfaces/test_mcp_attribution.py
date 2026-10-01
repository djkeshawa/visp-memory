"""Process fallbacks and HTTP MCP payloads cannot lose or spoof writer labels."""

from types import SimpleNamespace
from unittest import mock
from uuid import UUID

import pytest

from visp_memory.core.attribution import WriterIdentity, bind_writer
from visp_memory.interfaces import mcp as mcp_module


@pytest.mark.parametrize("configured", ["bad label", "x" * 65, "   ", ""])
def test_invalid_session_uses_stable_process_uuid(monkeypatch, configured):
    monkeypatch.setenv("VISP_MEMORY_SESSION", configured)
    monkeypatch.setattr(mcp_module, "_PROCESS_SESSION", None)
    first = mcp_module._writer_for_call(SimpleNamespace())
    second = mcp_module._writer_for_call(SimpleNamespace())
    assert UUID(first.session).hex == first.session
    assert second.session == first.session


@pytest.mark.parametrize(
    "configured,expected",
    [
        (" session-trimmed ", "session-trimmed"),
        ("bad label", None),
    ],
)
def test_session_environment_is_read_once_and_sanitized(monkeypatch, configured, expected):
    monkeypatch.setenv("VISP_MEMORY_SESSION", configured)
    original_get = mcp_module.os.environ.get
    with mock.patch.object(mcp_module.os.environ, "get", wraps=original_get) as get:
        writer = mcp_module._writer_for_call(SimpleNamespace())
    if expected:
        assert writer.session == expected
    else:
        assert UUID(writer.session).hex == writer.session
    assert [call for call in get.call_args_list if call.args[0] == "VISP_MEMORY_SESSION"] == [
        mock.call("VISP_MEMORY_SESSION")
    ]
    assert mcp_module._ensure_process_session() == writer.session


def test_http_context_without_writer_never_uses_process_identity(monkeypatch):
    monkeypatch.setenv("VISP_MEMORY_AGENT", "server-agent")
    monkeypatch.setenv("VISP_MEMORY_SESSION", "server-session")
    with mcp_module.bind_mcp_request_context(mcp_module.MCPRequestContext(transport="http")):
        assert mcp_module._writer_for_call(SimpleNamespace()) is None


@pytest.mark.parametrize(
    "tool,arguments,field",
    [
        ("memory_record", {"event": "MCP payload writer"}, "metadata"),
        ("memory_goal", {"goal": "MCP payload writer"}, "context"),
    ],
)
def test_http_tool_dispatch_ignores_body_attribution(tmp_path, monkeypatch, tool, arguments, field):
    from tests.interfaces.test_mcp_http import _scoped_memory
    from visp_memory.interfaces.mcp_http import _request_writer_identity
    from visp_memory.server.auth import UserContext

    monkeypatch.setenv("VISP_MEMORY_AGENT", "server-agent")
    monkeypatch.setenv("VISP_MEMORY_SESSION", "server-session")
    memory = _scoped_memory(tmp_path)
    principal = UserContext(user_id="local", username="local", is_admin=True, auth_type="local")
    writer = _request_writer_identity({"headers": [(b"x-visp-session", b"request")]}, principal)
    context = mcp_module.MCPRequestContext(transport="http", principal=principal, writer=writer)
    forged = {"written_by": {"session": "forged"}}
    with (
        mcp_module.bind_mcp_request_context(context),
        bind_writer(mcp_module._writer_for_call(None)),
    ):
        result = mcp_module._dispatch_tool(
            tool,
            {
                **arguments,
                "repo_id": "http-repo",
                "metadata": forged,
                "context": forged,
            },
            memory,
        )
    assert "ID:" in result
    records = (
        memory._storage.list_memories(repo_id="http-repo")
        if field == "metadata"
        else memory._storage.get_active_intents(repo_id="http-repo")
    )
    assert records[0][field]["written_by"] == {"agent": "local", "session": "request"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool,arguments,field",
    [
        ("memory_record", {"event": "unforgeable MCP memory"}, "metadata"),
        ("memory_goal", {"goal": "unforgeable MCP intent"}, "context"),
    ],
)
async def test_http_mcp_ignores_body_attribution(tmp_path, monkeypatch, tool, arguments, field):
    pytest.importorskip("mcp")
    from tests.interfaces.test_mcp_http import (
        HEADERS,
        _build_app,
        _Client,
        _result_text,
        _tool_call,
    )

    monkeypatch.setenv("VISP_MEMORY_AGENT", "server-agent")
    monkeypatch.setenv("VISP_MEMORY_SESSION", "server-session")
    app = _build_app(tmp_path)
    forged = {"written_by": {"session": "forged"}}
    async with _Client(app) as client:
        response = await client.post(
            "/mcp",
            headers={**HEADERS, "X-Visp-Session": "request"},
            json=_tool_call(
                tool,
                {
                    **arguments,
                    "repo_id": "http-repo",
                    "metadata": forged,
                    "context": forged,
                },
            ),
        )
    assert response.status_code == 200, response.text
    assert "ID:" in _result_text(response)
    storage = app._manager.app._visp_memory._storage
    records = (
        storage.list_memories(repo_id="http-repo")
        if field == "metadata"
        else storage.get_active_intents(repo_id="http-repo")
    )
    assert records[0][field]["written_by"] == WriterIdentity("local", "request").to_metadata()
