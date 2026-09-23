"""Focused tests for MCP dispatch, authorization preflight, and resources."""

import json
from types import SimpleNamespace
from unittest import mock

import pytest
from fastapi import HTTPException

from visp_memory.interfaces import mcp
from visp_memory.interfaces.mcp import (
    MCPAuthorizationError,
    MCPRequestContext,
    _dispatch_tool,
    _http_authorize_repo,
    _http_preflight,
    _http_validate_record_ids,
    _read_http_resource,
    bind_mcp_request_context,
    current_mcp_request_context,
    handle_tool,
)
from visp_memory.server.auth import UserContext


class _Memory:
    config = SimpleNamespace(repo_id="configured-repo")
    _storage = SimpleNamespace()


def test_http_resource_reader_formats_every_scoped_resource(monkeypatch):
    class Semantic:
        def get_warnings(self, *, repo_id):
            assert repo_id == "repo-a"
            return [{"content": "avoid force-push"}]

        def get_conventions(self, *, repo_id):
            assert repo_id == "repo-a"
            return [{"content": "run tests first"}]

    class Intent:
        def get_active(self, *, repo_id):
            assert repo_id == "repo-a"
            return [{"priority": 3, "description": "ship safely"}]

        def summarize(self, *, repo_id):
            assert repo_id == "repo-a"
            return {
                "current_task": {"description": "audit auth"},
                "focus": {"description": "MCP boundary"},
                "constraints": ["no secrets"],
            }

    class Episodic:
        def recent(self, *, limit, repo_id):
            assert (limit, repo_id) == (5, "repo-a")
            return [{"category": "note", "content": "checked endpoint"}]

    class Memory:
        semantic = Semantic()
        intent = Intent()
        episodic = Episodic()

        def context(self, *, format, repo_id):
            assert (format, repo_id) == ("text", "repo-a")
            return "repository context"

        def stats(self, *, repo_id):
            return {"repo_id": repo_id, "total": 2}

    monkeypatch.setattr(mcp, "_http_authorize_repo", lambda repo, memory, write: repo)
    memory = Memory()

    assert _read_http_resource("memory://repo/repo-a/context", memory) == "repository context"
    assert _read_http_resource("memory://repo/repo-a/warnings", memory) == "- avoid force-push"
    assert _read_http_resource("memory://repo/repo-a/goals", memory) == "[CRITICAL] ship safely"
    assert _read_http_resource("memory://repo/repo-a/conventions", memory) == "- run tests first"
    assert json.loads(_read_http_resource("memory://repo/repo-a/stats", memory)) == {
        "repo_id": "repo-a",
        "total": 2,
    }

    session = _read_http_resource("memory://repo/repo-a/session", memory)
    assert "audit auth" in session
    assert "MCP boundary" in session
    assert "- no secrets" in session
    assert "checked endpoint" in session

    class Recall:
        def __init__(self, received_memory, *, repo_id):
            assert received_memory is memory
            assert repo_id == "repo-a"

        def on_file_open(self, path):
            assert path == "src/auth.py"
            return {"matches": ["one"]}

        def format_injection(self, context, *, format):
            assert (context, format) == ({"matches": ["one"]}, "markdown")
            return "- relevant auth context"

    monkeypatch.setattr("visp_memory.recall.proactive.ProactiveRecall", Recall)
    assert _read_http_resource("memory://repo/repo-a/file/src%2Fauth.py", memory) == (
        "# Context for src/auth.py\n\n- relevant auth context"
    )


def test_http_resource_reader_handles_empty_collections_and_unknown_resource(monkeypatch):
    class EmptySemantic:
        def get_warnings(self, *, repo_id):
            return []

        def get_conventions(self, *, repo_id):
            return []

    memory = SimpleNamespace(
        semantic=EmptySemantic(),
        intent=SimpleNamespace(get_active=lambda *, repo_id: []),
    )
    monkeypatch.setattr(mcp, "_http_authorize_repo", lambda repo, memory, write: repo)

    assert _read_http_resource("memory://repo/repo-a/warnings", memory) == "No warnings."
    assert _read_http_resource("memory://repo/repo-a/goals", memory) == "No active goals."
    assert _read_http_resource("memory://repo/repo-a/conventions", memory) == (
        "No conventions established."
    )
    with pytest.raises(ValueError) as exc:
        _read_http_resource("memory://repo/repo-a/unknown", memory)
    assert json.loads(str(exc.value))["code"] == "resource_not_found"

    with pytest.raises(ValueError, match="authorization_denied"):
        _read_http_resource("memory://static", memory)


def test_http_resource_reader_hides_authorization_helper_details(monkeypatch):
    def deny(repo_id, memory, *, write):
        raise MCPAuthorizationError("caller supplied private repository metadata")

    monkeypatch.setattr(mcp, "_http_authorize_repo", deny)
    with pytest.raises(ValueError) as exc:
        _read_http_resource("memory://repo/repo-a/context", SimpleNamespace())

    payload = json.loads(str(exc.value))
    assert payload == {
        "success": False,
        "code": "authorization_denied",
        "request_id": None,
        "message": "The requested resource is not available to this token.",
    }
    assert "private repository" not in str(exc.value)


@pytest.mark.parametrize(
    ("name", "handler"),
    [
        ("memory_prepare_task", "_handle_task_brief"),
        ("memory_context", "_handle_context"),
        ("memory_recall", "_handle_search"),
        ("memory_remember", "_handle_search"),
        ("memory_relevant", "_handle_search"),
        ("memory_trace", "_handle_graph_recall"),
        ("memory_neighbors", "_handle_graph_recall"),
        ("memory_path", "_handle_graph_recall"),
        ("memory_why_relevant", "_handle_graph_recall"),
        ("memory_file_context", "_handle_proactive"),
        ("memory_find_error", "_handle_proactive"),
        ("memory_directory_context", "_handle_proactive"),
        ("memory_session_start", "_handle_workflow"),
        ("memory_before_change", "_handle_workflow"),
        ("memory_after_work", "_handle_workflow"),
        ("memory_record", "_handle_recording"),
        ("memory_decision", "_handle_recording"),
        ("memory_learn", "_handle_knowledge"),
        ("memory_warn", "_handle_knowledge"),
        ("memory_issue", "_handle_knowledge"),
        ("memory_goal", "_handle_intent"),
        ("memory_working_on", "_handle_intent"),
        ("memory_done", "_handle_intent"),
        ("memory_update_intent", "_handle_intent"),
        ("memory_close_intent", "_handle_intent"),
        ("memory_stats", "_handle_utility"),
        ("memory_list_warnings", "_handle_utility"),
        ("memory_list_intents", "_handle_utility"),
        ("memory_feedback_log", "_handle_feedback"),
        ("memory_feedback_inspect", "_handle_feedback"),
        ("memory_feedback_reset", "_handle_feedback"),
        ("memory_compress", "_handle_maintenance"),
        ("memory_decay", "_handle_maintenance"),
        ("memory_decay_preview", "_handle_maintenance"),
        ("memory_clear_goals", "_handle_maintenance"),
    ],
)
def test_dispatch_routes_each_public_tool_to_its_handler(monkeypatch, name, handler):
    captured = {}

    def fake_handler(*args):
        captured["args"] = args
        return f"handled:{name}"

    monkeypatch.setattr(mcp, handler, fake_handler)
    arguments = {}
    result = _dispatch_tool(name, arguments, _Memory())

    assert result == f"handled:{name}"
    if handler in {"_handle_task_brief", "_handle_context"}:
        assert captured["args"][0] is arguments
    else:
        assert captured["args"][0] == name
    if name in mcp._WRITE_TOOLS and handler != "_handle_workflow":
        assert arguments["repo_id"] == "configured-repo"


def test_dispatch_returns_a_stable_unknown_tool_message():
    assert _dispatch_tool("not-a-tool", {}, _Memory()) == "Unknown tool: not-a-tool"


def test_http_preflight_scopes_repo_reads_and_feedback_writes(monkeypatch):
    principal = UserContext(
        user_id="user-1",
        username="alice",
        scopes=["memory:read", "memory:write"],
        repo_ids=["repo-a"],
    )
    context = MCPRequestContext(
        transport="http", principal=principal, request_id="req", require_explicit_scope=True
    )
    memory = SimpleNamespace()
    calls = []

    def authorize(repo_id, received_memory, *, write):
        calls.append(("authorize", repo_id, write, received_memory))
        return repo_id

    def validate(args, received_memory, repo_id, received_principal):
        calls.append(("validate", args, repo_id, received_principal))

    monkeypatch.setattr(mcp, "_http_authorize_repo", authorize)
    monkeypatch.setattr(mcp, "_http_validate_record_ids", validate)
    with bind_mcp_request_context(context):
        _http_preflight("memory_recall", {"repo_id": "repo-a"}, memory)
        _http_preflight(
            "memory_recall", {"repo_id": "repo-a", "log_utility": True}, memory
        )

    assert calls[0][0:3] == ("authorize", "repo-a", False)
    assert calls[1][0:3] == ("validate", {"repo_id": "repo-a"}, "repo-a")
    assert calls[2][0:3] == ("authorize", "repo-a", True)
    assert calls[3][0:3] == (
        "validate",
        {"repo_id": "repo-a", "log_utility": True},
        "repo-a",
    )


def test_http_preflight_refuses_hidden_tools_and_skips_unscoped_non_repo_tools():
    context = MCPRequestContext(
        transport="http",
        principal=UserContext(user_id="u", username="u", scopes=["*"]),
        require_explicit_scope=True,
    )
    with bind_mcp_request_context(context):
        with pytest.raises(MCPAuthorizationError, match="Global maintenance"):
            _http_preflight("memory_clear_goals", {}, SimpleNamespace())
        # Model execution is not a repository tool and has its own provider policy.
        _http_preflight("memory_model_task", {}, SimpleNamespace())


def test_http_authorize_repo_applies_principal_scope_and_backend_policy(monkeypatch):
    storage = object()
    memory = SimpleNamespace(_storage=storage)
    principal = UserContext(
        user_id="u", username="alice", scopes=["memory:read", "memory:write"]
    )
    context = MCPRequestContext(transport="http", principal=principal)

    access = mock.Mock()
    writable = mock.Mock()
    monkeypatch.setattr("visp_memory.server.authorization.require_repo_scope_access", access)
    monkeypatch.setattr("visp_memory.server.authorization.require_repo_writable", writable)
    with bind_mcp_request_context(context):
        assert _http_authorize_repo("repo-a", memory, write=False) == "repo-a"
        assert _http_authorize_repo("repo-a", memory, write=True) == "repo-a"
    access.assert_called_once_with(storage, "repo-a", principal)
    writable.assert_called_once_with(storage, "repo-a", principal)

    with bind_mcp_request_context(
        MCPRequestContext(
            transport="http",
            principal=UserContext(user_id="u", username="u", scopes=[]),
        )
    ):
        with pytest.raises(MCPAuthorizationError, match="memory scope"):
            _http_authorize_repo("repo-a", memory, write=False)
        with pytest.raises(MCPAuthorizationError, match="memory scope"):
            _http_authorize_repo("repo-a", memory, write=True)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (HTTPException(status_code=409, detail="archived repo"), "archived"),
        (HTTPException(status_code=400, detail="repo_id is required"), "explicit repo_id"),
        (HTTPException(status_code=403, detail="private backend detail"), "not available"),
    ],
)
def test_http_authorize_repo_sanitizes_backend_refusals(monkeypatch, error, expected):
    principal = UserContext(user_id="u", username="u", scopes=["memory:read"])
    memory = SimpleNamespace(_storage=object())
    monkeypatch.setattr(
        "visp_memory.server.authorization.require_repo_scope_access",
        mock.Mock(side_effect=error),
    )
    with bind_mcp_request_context(MCPRequestContext(transport="http", principal=principal)):
        with pytest.raises(MCPAuthorizationError, match=expected):
            _http_authorize_repo("repo-a", memory, write=False)


def test_http_authorize_repo_requires_principal_and_explicit_repo():
    memory = SimpleNamespace(_storage=object())
    with bind_mcp_request_context(MCPRequestContext(transport="http")):
        with pytest.raises(MCPAuthorizationError, match="principal"):
            _http_authorize_repo("repo-a", memory, write=False)

    principal = UserContext(user_id="u", username="u", scopes=["memory:read"])
    with bind_mcp_request_context(MCPRequestContext(transport="http", principal=principal)):
        with pytest.raises(MCPAuthorizationError, match="explicit repo_id"):
            _http_authorize_repo("", memory, write=False)


def test_http_validate_record_ids_checks_memory_and_intent_scope(monkeypatch):
    records = {
        "memory-a": {"id": "memory-a", "repo_id": "repo-a", "metadata": {}},
        "memory-b": {"id": "memory-b", "repo_id": "repo-b", "metadata": {}},
    }
    intents = [
        {"id": "intent-a", "repo_id": "repo-a", "context": {}},
        {"id": "intent-b", "repo_id": "repo-b", "context": {}},
    ]
    storage = SimpleNamespace(
        get_memory=lambda memory_id: records.get(memory_id),
        get_active_intents=lambda **kwargs: intents,
    )
    memory = SimpleNamespace(_storage=storage)
    principal = UserContext(user_id="u", username="u", scopes=["memory:read"])
    allow = mock.Mock(return_value=True)
    monkeypatch.setattr("visp_memory.server.authorization.can_access_scoped_record", allow)

    _http_validate_record_ids(
        {"memory_ids": ["memory-a"], "intent_id": "intent-a"},
        memory,
        "repo-a",
        principal,
    )
    assert allow.call_count == 2

    for args in (
        {"memory_id": "missing"},
        {"memory_id": "memory-b"},
        {"intent_id": "intent-b"},
    ):
        with pytest.raises(MCPAuthorizationError, match="not available"):
            _http_validate_record_ids(args, memory, "repo-a", principal)

    allow.return_value = False
    with pytest.raises(MCPAuthorizationError, match="memory"):
        _http_validate_record_ids({"memory_id": "memory-a"}, memory, "repo-a", principal)


@pytest.mark.asyncio
async def test_handle_tool_preserves_request_context_in_worker_thread(monkeypatch):
    observed = {}

    def fake_dispatch(name, args, memory):
        observed["context"] = current_mcp_request_context()
        return "worker-result"

    monkeypatch.setattr(mcp, "_dispatch_tool", fake_dispatch)
    context = MCPRequestContext(
        transport="http", request_id="req-thread", require_explicit_scope=True
    )
    with bind_mcp_request_context(context):
        result = await handle_tool("memory_stats", {}, _Memory())

    assert result == "worker-result"
    assert observed["context"] == context
    assert current_mcp_request_context().transport == "stdio"
