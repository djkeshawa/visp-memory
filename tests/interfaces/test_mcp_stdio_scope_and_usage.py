"""Regressions from the MCP module audit.

- A stdio process pinned to one project reads and writes only that project:
  resources, the list/feedback tools, and ID-addressed tools must not reach
  another repository or the quarantine bucket.
- Resource reads and prompts run off the event loop, like tool calls.
- A deliberate argument refusal reaches the model as ``invalid_arguments``;
  any other exception stays redacted.
- The HTTP bind guard follows enforced authentication, not the static token.
- Tool limits advertise bounds, so oversized values are refused up front.
"""

import json
import threading
from types import ModuleType, SimpleNamespace
from unittest import mock

import pytest

from tests.mcp_client import call_tool
from visp_memory import Memory, MemoryConfig

pytest.importorskip("mcp")

from visp_memory.interfaces import mcp as mcp_module  # noqa: E402
from visp_memory.interfaces.mcp import (  # noqa: E402
    MCPRequestContext,
    _refuse_stdio_foreign_record_ids,
    bind_mcp_request_context,
    create_mcp_server,
)


def _memory(tmp_path, repo_id):
    config = MemoryConfig(repo_id=repo_id)
    config.storage.data_dir = tmp_path
    config.embedding.provider = "noop"
    return Memory(config=config)


def _server(memory):
    with mock.patch("visp_memory.interfaces.mcp.Memory", return_value=memory):
        return create_mcp_server()


@pytest.fixture
def two_repos(tmp_path, monkeypatch):
    """repo-b holds secrets (plus a quarantined row); the server is pinned to repo-a."""
    monkeypatch.setenv("VISP_MEMORY_MCP_PROFILE", "full")
    monkeypatch.delenv("VISP_MEMORY_MCP_ALLOW_REPO_OVERRIDE", raising=False)
    other = _memory(tmp_path, "repo-b")
    ids = {
        "intent": other.goal("SECRET-B goal"),
        "memory": other.record("SECRET-B event"),
    }
    other.warn("db", "SECRET-B warning")
    other.learn("SECRET-B convention", category="preference")
    other.record_utility_feedback(memory_id=ids["memory"], event_type="used", repo_id="repo-b")
    other.semantic.warn(area="db", warning="QUARANTINED warning", repo_id=None)
    other.close()
    pinned = _memory(tmp_path, "repo-a")
    pinned.goal("A goal")
    ids["own_intent"] = pinned.goal("A second goal")
    yield pinned, ids
    pinned.close()


def _leaks(text):
    return any(marker in text for marker in ("SECRET-B", "repo-b", "QUARANTINED"))


# --- 1. stdio scope ----------------------------------------------------------


@pytest.mark.asyncio
async def test_stdio_resources_read_only_the_pinned_repository(two_repos):
    from mcp import Client

    memory, _ = two_repos
    server = _server(memory)
    async with Client(server) as client:
        for uri in [
            "memory://context",
            "memory://warnings",
            "memory://goals",
            "memory://conventions",
            "memory://session",
            "memory://stats",
        ]:
            text = (await client.read_resource(uri)).contents[0].text
            assert not _leaks(text), f"{uri} read outside repo-a: {text}"
        goals = (await client.read_resource("memory://goals")).contents[0].text
    assert "A goal" in goals


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool", ["memory_list_intents", "memory_list_warnings", "memory_feedback_inspect"]
)
async def test_stdio_list_and_feedback_tools_default_to_the_pinned_repository(two_repos, tool):
    memory, _ = two_repos
    result = await call_tool(_server(memory), tool, {})
    text = result.content[0].text
    assert not _leaks(text), f"{tool} read outside repo-a: {text}"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool,arguments",
    [
        ("memory_update_intent", {"intent_id": "{intent}", "description": "rewritten"}),
        ("memory_close_intent", {"intent_id": "{intent}"}),
        ("memory_neighbors", {"memory_id": "{memory}"}),
        ("memory_feedback_log", {"memory_id": "{memory}", "event_type": "used"}),
        ("memory_feedback_inspect", {"memory_id": "{memory}"}),
    ],
)
async def test_stdio_refuses_ids_from_another_repository(two_repos, tool, arguments):
    memory, ids = two_repos
    arguments = {key: value.format(**ids) for key, value in arguments.items()}

    result = await call_tool(_server(memory), tool, arguments)

    text = result.content[0].text
    assert text.startswith("Refused:"), text
    assert "SECRET-B" not in text
    intents = memory._storage.get_active_intents(repo_id="repo-b", status="all")
    assert [intent["description"] for intent in intents] == ["SECRET-B goal"]


@pytest.mark.asyncio
async def test_stdio_still_updates_an_intent_in_its_own_repository(two_repos):
    memory, ids = two_repos
    result = await call_tool(
        _server(memory),
        "memory_update_intent",
        {"intent_id": ids["own_intent"], "description": "renamed"},
    )
    assert result.content[0].text == f"Intent updated: {ids['own_intent']}"


@pytest.mark.asyncio
async def test_stdio_override_permits_ids_from_the_named_repository(two_repos, monkeypatch):
    memory, ids = two_repos
    monkeypatch.setenv("VISP_MEMORY_MCP_ALLOW_REPO_OVERRIDE", "true")
    server = _server(memory)

    unnamed = await call_tool(
        server, "memory_update_intent", {"intent_id": ids["intent"], "description": "x"}
    )
    named = await call_tool(
        server,
        "memory_update_intent",
        {"intent_id": ids["intent"], "repo_id": "repo-b", "description": "deliberate"},
    )

    assert unnamed.content[0].text.startswith("Refused:")
    assert named.content[0].text == f"Intent updated: {ids['intent']}"


def test_an_unscoped_project_has_no_pin_to_enforce(tmp_path):
    other = _memory(tmp_path, "repo-b")
    intent_id = other.goal("B goal")
    unscoped = _memory(tmp_path, None)
    with bind_mcp_request_context(MCPRequestContext(transport="stdio")):
        assert _refuse_stdio_foreign_record_ids({"intent_id": intent_id}, unscoped) is None


# --- 2. resources and prompts off the event loop ------------------------------


@pytest.mark.asyncio
async def test_http_resource_reads_run_on_a_worker_thread(tmp_path, monkeypatch):
    from tests.interfaces.test_mcp_http import HEADERS, _build_app, _Client, _rpc

    app = _build_app(tmp_path)
    memory = app._manager.app._visp_memory
    threads = []
    real_stats = type(memory).stats

    def recording_stats(self, *args, **kwargs):
        threads.append(threading.current_thread())
        return real_stats(self, *args, **kwargs)

    monkeypatch.setattr(type(memory), "stats", recording_stats)
    async with _Client(app) as client:
        response = await client.post(
            "/mcp",
            headers=HEADERS,
            json=_rpc("resources/read", {"uri": "memory://repo/http-repo/stats"}),
        )

    assert response.status_code == 200, response.text
    assert threads and all(t is not threading.main_thread() for t in threads)


@pytest.mark.asyncio
async def test_stdio_prompts_run_on_a_worker_thread(tmp_path, monkeypatch):
    from mcp import Client

    memory = _memory(tmp_path, "repo-a")
    threads = []
    real_context = type(memory).context

    def recording_context(self, *args, **kwargs):
        threads.append(threading.current_thread())
        return real_context(self, *args, **kwargs)

    monkeypatch.setattr(type(memory), "context", recording_context)
    async with Client(_server(memory)) as client:
        await client.get_prompt("start_session", {})

    assert threads and all(t is not threading.main_thread() for t in threads)


# --- 3. usage errors -----------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool,arguments,fragment",
    [
        ("memory_context", {"ranking_strategy": "hybrid"}, "query is required"),
        ("memory_learn", {"knowledge": "k", "epistemic_status": "observed"}, "epistemic"),
        (
            "memory_update_intent",
            {
                "intent_id": "x",
                "description": "d",
                "workflow_report": {
                    "source": "ci",
                    "task_id": "t",
                    "event_id": "e",
                    "revision": 1,
                    "status": "active",
                    "summary": "s",
                },
            },
            "workflow report separately",
        ),
    ],
)
async def test_usage_errors_name_the_problem(tmp_path, monkeypatch, tool, arguments, fragment):
    monkeypatch.setenv("VISP_MEMORY_MCP_PROFILE", "full")
    result = await call_tool(_server(_memory(tmp_path, "proj")), tool, arguments)

    payload = json.loads(result.content[0].text)
    assert result.is_error is True
    assert payload["code"] == "invalid_arguments"
    assert fragment in payload["message"]


@pytest.mark.asyncio
async def test_other_value_errors_stay_redacted(tmp_path, monkeypatch):
    async def explode(*args, **kwargs):
        raise ValueError("database password = super-secret")

    monkeypatch.setattr(mcp_module, "handle_tool", explode)
    result = await call_tool(_server(_memory(tmp_path, "proj")), "memory_recall", {"query": "q"})

    text = result.content[0].text
    assert "super-secret" not in text
    assert json.loads(text)["code"] == "request_failed"


@pytest.mark.asyncio
async def test_http_usage_error_is_returned_and_correlated(tmp_path, monkeypatch):
    from tests.interfaces.test_mcp_http import HEADERS, _build_app, _Client, _tool_call

    monkeypatch.setenv("VISP_MEMORY_MCP_PROFILE", "full")
    app = _build_app(tmp_path)
    async with _Client(app) as client:
        response = await client.post(
            "/mcp",
            headers=HEADERS,
            json=_tool_call(
                "memory_context", {"repo_id": "http-repo", "ranking_strategy": "hybrid"}
            ),
        )

    result = response.json()["result"]
    payload = json.loads(result["content"][0]["text"])
    assert result["isError"] is True
    assert payload["code"] == "invalid_arguments"
    assert payload["request_id"] == response.headers["x-request-id"]


@pytest.mark.asyncio
async def test_session_start_without_a_scope_is_refused_not_failed(tmp_path, monkeypatch):
    monkeypatch.setenv("VISP_MEMORY_MCP_PROFILE", "full")
    memory = _memory(tmp_path, None)
    result = await call_tool(_server(memory), "memory_session_start", {"task": "build"})

    assert result.content[0].text.startswith("Refused:")
    assert memory._storage.get_active_intents(repo_id=None, status="all") == []


# --- 4. HTTP bind guard -----------------------------------------------------


def _patch_main(monkeypatch, *, host, token, auth_enabled, data_dir):
    import sys

    import visp_memory.config
    import visp_memory.interfaces.mcp_http as mcp_http

    uvicorn = ModuleType("uvicorn")
    runs = []
    uvicorn.run = lambda *args, **kwargs: runs.append(kwargs)
    monkeypatch.setitem(sys.modules, "uvicorn", uvicorn)
    monkeypatch.setattr(mcp_http, "MCP_AVAILABLE", True)
    monkeypatch.setenv("VISP_MEMORY_MCP_HTTP_HOST", host)
    if token is None:
        monkeypatch.delenv("VISP_MEMORY_MCP_HTTP_TOKEN", raising=False)
    else:
        monkeypatch.setenv("VISP_MEMORY_MCP_HTTP_TOKEN", token)
    config = SimpleNamespace(
        server=SimpleNamespace(auth_enabled=auth_enabled),
        storage=SimpleNamespace(data_dir=data_dir),
    )
    monkeypatch.setattr(visp_memory.config, "load_config", lambda: config)
    monkeypatch.setattr(mcp_http, "build_http_app", lambda **kwargs: kwargs)
    return mcp_http, runs


def test_static_token_does_not_unlock_a_network_bind(tmp_path, monkeypatch, capsys):
    mcp_http, runs = _patch_main(
        monkeypatch, host="0.0.0.0", token="s3cret", auth_enabled=False, data_dir=tmp_path
    )

    assert mcp_http.main() == 1
    assert runs == []
    assert "does not authorize" in capsys.readouterr().out


def test_enforced_authentication_permits_a_network_bind(tmp_path, monkeypatch):
    mcp_http, runs = _patch_main(
        monkeypatch, host="0.0.0.0", token=None, auth_enabled=True, data_dir=tmp_path
    )

    assert mcp_http.main() == 0
    assert [run["host"] for run in runs] == ["0.0.0.0"]


# --- 5. bounded limits -----------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool,arguments",
    [
        ("memory_recall", {"query": "auth", "limit": 10**20}),
        ("memory_find_error", {"error_message": "auth", "limit": -1}),
        ("memory_feedback_inspect", {"limit": -1}),
        ("memory_trace", {"query": "auth", "depth": 10**6}),
        ("memory_trace", {"query": "auth", "token_budget": -5}),
        ("memory_neighbors", {"memory_id": "x", "limit": 10**20}),
        ("memory_path", {"source_id": "a", "target_id": "b", "max_hops": 100}),
        ("memory_why_relevant", {"query": "q", "memory_id": "x", "limit": 0}),
    ],
)
async def test_out_of_range_limits_are_refused_by_the_schema(
    tmp_path, monkeypatch, tool, arguments
):
    monkeypatch.setenv("VISP_MEMORY_MCP_PROFILE", "full")
    result = await call_tool(_server(_memory(tmp_path, "proj")), tool, arguments)

    assert result.is_error is True
    assert result.content[0].text.startswith("Input validation error")
