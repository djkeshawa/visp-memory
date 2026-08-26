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

import asyncio
import json
import re
import time
from unittest import mock

import httpx
import pytest

from visp_memory import Memory, MemoryConfig
from visp_memory.core.model_router import ModelUnavailableError

pytest.importorskip("mcp")

from visp_memory.interfaces.mcp import create_mcp_server  # noqa: E402
from visp_memory.interfaces.mcp_http import build_http_app, main  # noqa: E402
from visp_memory.server.auth_store import AuthStore  # noqa: E402

HEADERS = {
    "content-type": "application/json",
    "accept": "application/json, text/event-stream",
}


def _scoped_memory(tmp_path):
    # An MCP server runs against an initialized project, which always has a
    # repository scope; without one every write tool is correctly refused.
    config = MemoryConfig(repo_id="http-repo")
    config.server.auth_enabled = False
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
                json=_tool_call(
                    "memory_record",
                    {"event": "written over stateless http", "repo_id": "http-repo"},
                ),
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
                json=_tool_call(
                    "memory_record",
                    {"event": "first request wrote this", "repo_id": "http-repo"},
                ),
                headers=HEADERS,
            )
            second = await client.post(
                "/mcp",
                json=_tool_call(
                    "memory_recall",
                    {"query": "first request wrote", "repo_id": "http-repo"},
                ),
                headers=HEADERS,
            )
        assert first.status_code == 200
        # The second request carried no session identity, yet reads what the
        # first durably wrote: state lives in storage, not in the transport.
        assert "first request wrote this" in _result_text(second)

    @pytest.mark.asyncio
    async def test_repo_scope_is_required_before_an_http_write(self, tmp_path):
        app = _build_app(tmp_path)
        async with _Client(app) as client:
            response = await client.post(
                "/mcp",
                json=_tool_call("memory_record", {"event": "must not be stored"}),
                headers=HEADERS,
            )
        assert response.status_code == 200
        payload = json.loads(_result_text(response))
        assert payload["code"] == "authorization_denied"
        assert re.fullmatch(r"[0-9a-f]{32}", payload["request_id"])
        assert "repo_id" in payload["message"]

    @pytest.mark.asyncio
    async def test_http_resources_are_hidden(self, tmp_path):
        app = _build_app(tmp_path)
        async with _Client(app) as client:
            response = await client.post("/mcp", json=_rpc("resources/list"), headers=HEADERS)
        assert response.status_code == 200
        payload = json.loads(response.text)
        assert payload["result"]["resources"] == []

    @pytest.mark.asyncio
    async def test_http_resources_use_scoped_templates_and_reject_static_uris(self, tmp_path):
        app = _build_app(tmp_path)
        async with _Client(app) as client:
            listed = await client.post(
                "/mcp", json=_rpc("resources/templates/list"), headers=HEADERS
            )
            scoped = await client.post(
                "/mcp",
                json=_rpc(
                    "resources/read",
                    {"uri": "memory://repo/http-repo/stats"},
                ),
                headers=HEADERS,
            )
            static = await client.post(
                "/mcp",
                json=_rpc("resources/read", {"uri": "memory://stats"}),
                headers=HEADERS,
            )
        templates = json.loads(listed.text)["result"]["resourceTemplates"]
        assert any("memory://repo/{repo_id}/stats" == item["uriTemplate"] for item in templates)
        scoped_payload = json.loads(scoped.text)
        assert scoped_payload["result"]["contents"][0]["mimeType"] == "text/plain"
        static_payload = json.loads(static.text)
        assert "error" in static_payload
        assert "request_id" in static_payload["error"]["data"]
        assert static_payload["error"]["data"]["code"] == "authorization_denied"

    @pytest.mark.asyncio
    async def test_http_global_maintenance_is_hidden_and_refused(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISP_MEMORY_MCP_PROFILE", "full")
        app = _build_app(tmp_path)
        async with _Client(app) as client:
            listed = await client.post("/mcp", json=_rpc("tools/list"), headers=HEADERS)
            called = await client.post(
                "/mcp",
                json=_tool_call("memory_clear_goals", {"confirm": True}),
                headers=HEADERS,
            )
        names = {item["name"] for item in json.loads(listed.text)["result"]["tools"]}
        assert "memory_clear_goals" not in names
        assert "Global maintenance" in _result_text(called)

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
        body = right.json()
        assert body.get("request_id") or body.get("result")

    @pytest.mark.asyncio
    async def test_auth_transport_error_has_opaque_request_id_and_stable_code(self, tmp_path):
        app = _build_app(tmp_path, token="s3cret")
        async with _Client(app) as client:
            response = await client.post("/mcp", json=_rpc("tools/list"), headers=HEADERS)
        payload = response.json()
        assert payload["code"] == "authentication_required"
        assert payload["error"] == "authentication_required"
        assert re.fullmatch(r"[0-9a-f]{32}", payload["request_id"])
        assert response.headers["x-request-id"] == payload["request_id"]

    @pytest.mark.asyncio
    async def test_pat_principal_cannot_use_another_repository(self, tmp_path):
        store = AuthStore(tmp_path / "auth.db")
        account = store.create_account(
            username="scope-user", password="a-secure-password-123", user_id="user-a"
        )
        _, token = store.create_token(
            user_id=account["id"],
            name="scope-test",
            scopes=["memory:read", "memory:write"],
            repo_ids=["repo-a"],
        )
        app = _build_app(tmp_path, auth_store=store)
        headers = {**HEADERS, "authorization": f"Bearer {token}"}
        async with _Client(app) as client:
            allowed = await client.post(
                "/mcp",
                json=_tool_call("memory_record", {"event": "allowed", "repo_id": "repo-a"}),
                headers=headers,
            )
            denied = await client.post(
                "/mcp",
                json=_tool_call("memory_record", {"event": "denied", "repo_id": "repo-b"}),
                headers=headers,
            )
        assert "Recorded event" in _result_text(allowed)
        denied_text = _result_text(denied)
        assert "authorization_denied" in denied_text
        assert "not available" in denied_text

    @pytest.mark.asyncio
    async def test_record_id_checks_metadata_scope_for_feedback_paths(self, tmp_path):
        store = AuthStore(tmp_path / "auth.db")
        account = store.create_account(
            username="feedback-scope-user",
            password="a-secure-password-123",
            user_id="user-feedback",
            team_id="team-a",
        )
        _, token = store.create_token(
            user_id=account["id"],
            name="feedback-scope-test",
            scopes=["memory:read", "memory:write"],
            repo_ids=["repo-a"],
        )
        app = _build_app(tmp_path, auth_store=store)
        memory = app._manager.app._visp_memory
        memory_id = memory.record("private team memory", repo_id="repo-a")
        memory._storage.update_memory(memory_id, metadata={"team_id": "team-b"})
        headers = {**HEADERS, "authorization": f"Bearer {token}"}

        async with _Client(app) as client:
            response = await client.post(
                "/mcp",
                json=_tool_call(
                    "memory_feedback_log",
                    {"memory_id": memory_id, "event_type": "used", "repo_id": "repo-a"},
                ),
                headers=headers,
            )

        payload = json.loads(_result_text(response))
        assert payload["code"] == "authorization_denied"
        assert memory._storage.inspect_recall_utility(repo_id="repo-a")["summary"][
            "total_events"
        ] == 0

    @pytest.mark.asyncio
    async def test_feedback_http_scope_is_required_and_limited_to_one_repository(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("VISP_MEMORY_MCP_PROFILE", "full")
        app = _build_app(tmp_path)
        memory = app._manager.app._visp_memory
        repo_a_memory = memory.record("repo-a feedback", repo_id="repo-a")
        repo_b_memory = memory.record("repo-b feedback", repo_id="repo-b")
        memory.record_utility_feedback(repo_a_memory, "used", repo_id="repo-a")
        memory.record_utility_feedback(repo_b_memory, "used", repo_id="repo-b")

        async with _Client(app) as client:
            listed = await client.post(
                "/mcp", json=_rpc("tools/list"), headers=HEADERS
            )
            unscoped_inspect = await client.post(
                "/mcp",
                json=_tool_call("memory_feedback_inspect", {}),
                headers=HEADERS,
            )
            scoped_inspect = await client.post(
                "/mcp",
                json=_tool_call(
                    "memory_feedback_inspect",
                    {"repo_id": "repo-a"},
                ),
                headers=HEADERS,
            )
            unscoped_reset = await client.post(
                "/mcp",
                json=_tool_call("memory_feedback_reset", {"confirm": True}),
                headers=HEADERS,
            )
            scoped_reset = await client.post(
                "/mcp",
                json=_tool_call(
                    "memory_feedback_reset",
                    {"repo_id": "repo-a", "confirm": True},
                ),
                headers=HEADERS,
            )

        tools = {
            item["name"]: item for item in json.loads(listed.text)["result"]["tools"]
        }
        for name in ("memory_feedback_inspect", "memory_feedback_reset"):
            assert "repo_id" in tools[name]["inputSchema"]["required"]

        unscoped_text = _result_text(unscoped_inspect)
        assert json.loads(unscoped_text)["code"] == "authorization_denied"
        assert repo_a_memory not in unscoped_text
        assert repo_b_memory not in unscoped_text

        scoped_report = json.loads(_result_text(scoped_inspect))
        assert scoped_report["summary"]["total_events"] == 1
        assert [event["memory_id"] for event in scoped_report["events"]] == [repo_a_memory]
        assert repo_b_memory not in _result_text(scoped_inspect)

        assert json.loads(_result_text(unscoped_reset))["code"] == "authorization_denied"
        assert "Deleted 1 feedback events" in _result_text(scoped_reset)
        assert memory._storage.inspect_recall_utility(repo_id="repo-b")["summary"][
            "total_events"
        ] == 1

    @pytest.mark.asyncio
    async def test_feedback_reset_requires_write_scope_over_http(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISP_MEMORY_MCP_PROFILE", "full")
        store = AuthStore(tmp_path / "auth.db")
        account = store.create_account(
            username="feedback-read-only",
            password="a-secure-password-123",
            user_id="user-feedback-read-only",
        )
        _, token = store.create_token(
            user_id=account["id"],
            name="feedback-read-only",
            scopes=["memory:read"],
            repo_ids=["repo-a"],
        )
        app = _build_app(tmp_path, auth_store=store)
        memory = app._manager.app._visp_memory
        memory_id = memory.record("read-only feedback", repo_id="repo-a")
        memory.record_utility_feedback(memory_id, "used", repo_id="repo-a")
        headers = {**HEADERS, "authorization": f"Bearer {token}"}

        async with _Client(app) as client:
            response = await client.post(
                "/mcp",
                json=_tool_call(
                    "memory_feedback_reset",
                    {"repo_id": "repo-a", "confirm": True},
                ),
                headers=headers,
            )

        payload = json.loads(_result_text(response))
        assert payload["code"] == "authorization_denied"
        assert memory._storage.inspect_recall_utility(repo_id="repo-a")["summary"][
            "total_events"
        ] == 1

    @pytest.mark.asyncio
    async def test_intent_id_checks_context_scope(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISP_MEMORY_MCP_PROFILE", "full")
        store = AuthStore(tmp_path / "auth.db")
        account = store.create_account(
            username="intent-context-user",
            password="a-secure-password-123",
            user_id="user-intent-context",
            team_id="team-a",
        )
        _, token = store.create_token(
            user_id=account["id"],
            name="intent-context-test",
            scopes=["memory:read", "memory:write"],
            repo_ids=["repo-a"],
        )
        app = _build_app(tmp_path, auth_store=store)
        memory = app._manager.app._visp_memory
        intent_id = memory.goal("private team intent", repo_id="repo-a")
        memory._storage.update_intent(intent_id, context={"team_id": "team-b"})
        headers = {**HEADERS, "authorization": f"Bearer {token}"}

        async with _Client(app) as client:
            response = await client.post(
                "/mcp",
                json=_tool_call(
                    "memory_update_intent",
                    {"intent_id": intent_id, "description": "must not update", "repo_id": "repo-a"},
                ),
                headers=headers,
            )

        payload = json.loads(_result_text(response))
        assert payload["code"] == "authorization_denied"
        assert memory._storage.get_active_intents(repo_id="repo-a")[0]["description"] == (
            "private team intent"
        )

    @pytest.mark.asyncio
    async def test_http_intent_mutation_must_match_the_explicit_repository(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("VISP_MEMORY_MCP_PROFILE", "full")
        store = AuthStore(tmp_path / "auth.db")
        account = store.create_account(
            username="intent-user", password="a-secure-password-123", user_id="user-intent"
        )
        _, token = store.create_token(
            user_id=account["id"],
            name="intent-test",
            scopes=["memory:read", "memory:write"],
            repo_ids=["repo-a", "repo-b"],
        )
        app = _build_app(tmp_path, auth_store=store)
        intent_id = app._manager.app._visp_memory.goal("keep scope", repo_id="repo-a")
        headers = {**HEADERS, "authorization": f"Bearer {token}"}
        async with _Client(app) as client:
            response = await client.post(
                "/mcp",
                json=_tool_call(
                    "memory_update_intent",
                    {"intent_id": intent_id, "description": "crossed", "repo_id": "repo-b"},
                ),
                headers=headers,
            )
        text = _result_text(response)
        assert "authorization_denied" in text
        assert "intent" in text
        stored = app._manager.app._visp_memory._storage.get_active_intents(
            repo_id="repo-a", status="all"
        )
        assert stored[0]["description"] == "keep scope"

    @pytest.mark.asyncio
    async def test_http_errors_do_not_echo_exception_text(self, tmp_path, monkeypatch):
        async def explode(*args, **kwargs):
            raise RuntimeError("database password = super-secret")

        monkeypatch.setattr("visp_memory.interfaces.mcp.handle_tool", explode)
        app = _build_app(tmp_path)
        async with _Client(app) as client:
            response = await client.post(
                "/mcp",
                json=_tool_call("memory_record", {"event": "x", "repo_id": "http-repo"}),
                headers=HEADERS,
            )
        text = _result_text(response)
        assert "super-secret" not in text
        payload = json.loads(text)
        assert payload["code"] == "request_failed"
        assert payload["request_id"] == response.headers["x-request-id"]
        assert "could not complete" in payload["message"].lower()

    @pytest.mark.asyncio
    async def test_sse_errors_are_sanitized_and_correlated(self, tmp_path):
        app = _build_app(tmp_path, json_response=False)
        async with _Client(app) as client:
            response = await client.post(
                "/mcp",
                json=_tool_call("memory_record", {"repo_id": "http-repo"}),
                headers=HEADERS,
            )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        request_id = response.headers["x-request-id"]
        assert re.fullmatch(r"[0-9a-f]{32}", request_id)
        data_lines = [
            line.removeprefix("data: ")
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        assert len(data_lines) == 1
        frame = json.loads(data_lines[0])
        error_text = frame["result"]["content"][0]["text"]
        error = json.loads(error_text)
        assert error["code"] == "request_failed"
        assert error["request_id"] == request_id
        assert "required property" not in response.text

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

    @pytest.mark.asyncio
    async def test_model_unavailable_error_is_sanitized_and_correlated(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISP_MEMORY_MCP_PROFILE", "full")

        def unavailable(*args, **kwargs):
            raise ModelUnavailableError("provider secret = do-not-disclose")

        monkeypatch.setattr("visp_memory.core.model_router.ModelRouter.complete", unavailable)
        app = _build_app(tmp_path)
        async with _Client(app) as client:
            response = await client.post(
                "/mcp",
                json=_tool_call("memory_model_task", {"task": "answer", "prompt": "hi"}),
                headers=HEADERS,
            )
        payload = json.loads(_result_text(response))
        assert payload["code"] == "model_unavailable"
        assert payload["request_id"] == response.headers["x-request-id"]
        assert "do-not-disclose" not in response.text
        assert "No server-side LLM provider is configured" in payload["message"]

    @pytest.mark.asyncio
    async def test_sync_model_fallback_does_not_block_other_http_tasks(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VISP_MEMORY_MCP_PROFILE", "full")

        def slow_complete(*args, **kwargs):
            time.sleep(0.1)
            return {"task": "answer", "provider": "test", "model": "test", "text": "ok"}

        monkeypatch.setattr(
            "visp_memory.core.model_router.ModelRouter.complete", slow_complete
        )
        app = _build_app(tmp_path)
        progressed = asyncio.Event()

        async def post_model_task():
            async with _Client(app) as client:
                return await client.post(
                    "/mcp",
                    json=_tool_call("memory_model_task", {"task": "answer", "prompt": "hi"}),
                    headers=HEADERS,
                )

        async def observe_loop():
            await asyncio.sleep(0.02)
            progressed.set()

        response, _ = await asyncio.gather(post_model_task(), observe_loop())
        assert response.status_code == 200
        assert progressed.is_set()


class TestMainGuards:
    def test_refuses_non_loopback_bind_without_token(self, monkeypatch, capsys):
        monkeypatch.setenv("VISP_MEMORY_MCP_HTTP_HOST", "0.0.0.0")
        monkeypatch.delenv("VISP_MEMORY_MCP_HTTP_TOKEN", raising=False)
        assert main() == 1
        out = capsys.readouterr().out
        assert "VISP_MEMORY_MCP_HTTP_TOKEN" in out
