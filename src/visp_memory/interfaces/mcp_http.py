"""Stateless streamable-HTTP transport for the Visp Memory MCP server.

One process serves every client. Each POST to ``/mcp`` is a self-contained
MCP request: no session is created, no session id is issued or required, and
two consecutive requests share nothing but the durable store. That is what
makes the deployment horizontally scalable — any replica can answer any
request — and it is possible because the tool surface was already stateless:
HTTP repository-dependent tools take ``repo_id`` per call, unscoped writes are
refused with the repair named, and all durable state lives in storage.

Usage:
    visp-memory-mcp-http

Configuration (environment):
    VISP_MEMORY_MCP_HTTP_HOST   Bind address (default 127.0.0.1).
    VISP_MEMORY_MCP_HTTP_PORT   Port (default 8848).
    VISP_MEMORY_MCP_HTTP_TOKEN  Optional compatibility bearer token. Required
                                to bind a non-loopback address. In an
                                authentication-enabled deployment, requests
                                must carry a valid ``llmm_`` personal access
                                token so every request gets its own principal.

Everything the stdio server reads still applies — ``VISP_MEMORY_MCP_PROFILE``,
the project config discovered from the working directory, and the
``VISP_MEMORY_*`` overrides. One deliberate difference from stdio: there is no
per-client working directory, so clients of a shared deployment must pass
``repo_id`` explicitly rather than lean on the server's ambient scope.

What statelessness costs: server-initiated sampling needs a live session, so
``memory_model_task`` always takes its server-side provider fallback here
(refusing with the repair when none is configured, never hanging).
"""

from __future__ import annotations

import contextlib
import hmac
import json
import logging
import os
import uuid
from typing import TYPE_CHECKING

import anyio

from visp_memory.interfaces.mcp import (
    MCP_AVAILABLE,
    MCPRequestContext,
    bind_mcp_request_context,
    create_mcp_server,
)

if TYPE_CHECKING:
    from visp_memory.server.auth import UserContext

logger = logging.getLogger("visp-memory-mcp-http")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8848

#: Addresses that may be bound without a bearer token.
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class StatelessMCPApp:
    """A deliberately small ASGI app: one endpoint, one auth check, one manager.

    Written against the ASGI protocol directly rather than through a routing
    framework: the whole surface is "POST /mcp", and framework routing brought
    real behavior we do not want — a 307 redirect on the bare ``/mcp`` path
    that not every MCP client follows.
    """

    def __init__(self, manager, token: str | None = None, *, auth_store=None, config=None):
        self._manager = manager
        self._token = token
        self._auth_store = auth_store
        self._config = config

    @contextlib.asynccontextmanager
    async def lifespan(self):
        """The session manager's task group, alive for the app's lifetime."""
        async with self._manager.run():
            yield

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            await self._serve_lifespan(receive, send)
            return
        if scope["type"] != "http":
            return
        request_id = uuid.uuid4().hex
        base_context = MCPRequestContext(
            transport="http",
            request_id=request_id,
            require_explicit_scope=True,
        )
        with bind_mcp_request_context(base_context):
            if scope.get("path", "").rstrip("/") != "/mcp":
                logger.info("MCP HTTP path not found request_id=%s", request_id)
                await _send_json(
                    send,
                    404,
                    _transport_error(
                        "not_found",
                        "The MCP endpoint is /mcp.",
                        request_id,
                    ),
                    request_id=request_id,
                )
                return
            try:
                principal = await anyio.to_thread.run_sync(self._authenticate, scope)
            except Exception:
                logger.exception("MCP HTTP authentication failed request_id=%s", request_id)
                await _send_json(
                    send,
                    500,
                    _transport_error(
                        "authentication_failed",
                        "The server could not authenticate the request; check server logs.",
                        request_id,
                    ),
                    request_id=request_id,
                )
                return
            if principal is None:
                logger.warning("MCP HTTP authentication denied request_id=%s", request_id)
                await _send_json(
                    send,
                    401,
                    _transport_error(
                        "authentication_required",
                        "Authentication is required for the MCP HTTP endpoint.",
                        request_id,
                    ),
                    request_id=request_id,
                )
                return

            request_context = MCPRequestContext(
                transport="http",
                principal=principal,
                request_id=request_id,
                require_explicit_scope=True,
            )
            with bind_mcp_request_context(request_context):
                response_started = False
                content_type = b""

                async def send_with_request_id(message):
                    nonlocal response_started, content_type
                    outgoing = message
                    if message["type"] == "http.response.start":
                        response_started = True
                        content_type = next(
                            (
                                value
                                for name, value in message.get("headers", [])
                                if name.lower() == b"content-type"
                            ),
                            b"",
                        )
                        headers = [
                            (name, value)
                            for name, value in message.get("headers", [])
                            if name.lower() != b"x-request-id"
                        ]
                        headers.append((b"x-request-id", request_id.encode("ascii")))
                        outgoing = {**message, "headers": headers}
                    elif message["type"] == "http.response.body":
                        body = message.get("body", b"")
                        if body:
                            outgoing = {
                                **message,
                                "body": _sanitize_mcp_body(body, request_id, content_type),
                            }
                    await send(outgoing)

                try:
                    await self._manager.handle_request(
                        scope, receive, send_with_request_id
                    )
                except Exception:
                    logger.exception(
                        "MCP HTTP transport failed request_id=%s", request_id
                    )
                    if not response_started:
                        await _send_json(
                            send,
                            500,
                            _transport_error(
                                "request_failed",
                                "The MCP request failed; check server logs.",
                                request_id,
                            ),
                            request_id=request_id,
                        )
                    return

    def _authenticate(self, scope) -> UserContext | None:
        """Resolve one request to a PAT principal or a deliberately local context."""
        from visp_memory.server.auth import UserContext

        if self._auth_store is not None:
            token = _bearer_token(scope)
            if token is None:
                return None
            principal = self._auth_store.authenticate_token(token)
            if not principal:
                return None
            return UserContext(**principal, auth_type="pat")

        if self._token is not None:
            if not _authorized(scope, self._token):
                return None
            # This branch is retained for injected/unit-test apps that do not
            # have the API auth extra. Production main() always supplies an
            # AuthStore, so a shared static token cannot authorize repo calls.
            return UserContext(
                user_id="http-token",
                username="http-token",
                auth_type="legacy",
                scopes=[],
            )

        if self._config is not None and self._config.server.auth_enabled:
            return None
        return UserContext(
            user_id="local",
            username="local",
            is_admin=True,
            auth_type="local",
        )

    async def _serve_lifespan(self, receive, send):
        lifespan = None
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                lifespan = self.lifespan()
                await lifespan.__aenter__()
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                if lifespan is not None:
                    await lifespan.__aexit__(None, None, None)
                await send({"type": "lifespan.shutdown.complete"})
                return


def _transport_error(code: str, message: str, request_id: str) -> bytes:
    """Serialize a safe transport error with its request correlation ID."""
    return json.dumps(
        {
            "success": False,
            "error": code,
            "code": code,
            "request_id": request_id,
            "message": message,
        }
    ).encode("utf-8")


def _safe_error_payload(code: str, message: str, request_id: str) -> dict:
    return {
        "success": False,
        "code": code,
        "request_id": request_id,
        "message": message,
    }


def _sanitize_json_rpc_payload(payload: object, request_id: str) -> object:
    """Attach correlation metadata and remove SDK/provider error detail."""
    if not isinstance(payload, dict):
        return payload

    error = payload.get("error")
    if isinstance(error, dict):
        raw_message = str(error.get("message") or "")
        try:
            safe = json.loads(raw_message)
        except (TypeError, ValueError):
            safe = None
        if (
            isinstance(safe, dict)
            and safe.get("success") is False
            and safe.get("code")
        ):
            safe["request_id"] = request_id
            error["message"] = str(safe.get("message") or "The MCP request failed.")
            error["data"] = safe
        else:
            logger.error(
                "MCP transport error request_id=%s detail=%s", request_id, raw_message
            )
            error["message"] = "The MCP request failed; check server logs."
            error["data"] = _safe_error_payload("request_failed", error["message"], request_id)
        payload["error"] = error

    result = payload.get("result")
    if isinstance(result, dict) and result.get("isError"):
        for item in result.get("content") or []:
            if not isinstance(item, dict) or item.get("type") != "text":
                continue
            raw_text = str(item.get("text") or "")
            try:
                safe = json.loads(raw_text)
            except (TypeError, ValueError):
                safe = None
            if isinstance(safe, dict) and safe.get("success") is False and safe.get("code"):
                safe["request_id"] = request_id
            elif "repo_id" in raw_text:
                logger.warning(
                    "MCP input scope validation failed request_id=%s detail=%s",
                    request_id,
                    raw_text,
                )
                safe = _safe_error_payload(
                    "authorization_denied",
                    "An explicit repo_id is required for stateless HTTP requests.",
                    request_id,
                )
            else:
                logger.error(
                    "MCP tool error request_id=%s detail=%s", request_id, raw_text
                )
                safe = _safe_error_payload(
                    "request_failed",
                    "The MCP request failed; check server logs.",
                    request_id,
                )
            item["text"] = json.dumps(safe)
    return payload


def _sanitize_mcp_body(body: bytes, request_id: str, content_type: bytes) -> bytes:
    """Sanitize JSON-RPC error details while preserving successful MCP payloads."""
    if b"event-stream" in content_type.lower():
        return _sanitize_sse_body(body, request_id)
    if b"json" not in content_type.lower():
        return body
    try:
        payload = json.loads(body)
    except (TypeError, ValueError):
        return body
    return json.dumps(_sanitize_json_rpc_payload(payload, request_id)).encode("utf-8")


def _sanitize_sse_body(body: bytes, request_id: str) -> bytes:
    """Sanitize JSON-RPC error envelopes carried in streamable-HTTP SSE frames."""
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        logger.error("MCP SSE body was not UTF-8 request_id=%s", request_id)
        return body

    sanitized_lines = []
    for line in text.splitlines(keepends=True):
        if not line.startswith("data:"):
            sanitized_lines.append(line)
            continue

        line_body = line[len("data:") :]
        newline = ""
        if line_body.endswith("\r\n"):
            line_body, newline = line_body[:-2], "\r\n"
        elif line_body.endswith(("\n", "\r")):
            line_body, newline = line_body[:-1], line_body[-1]
        try:
            payload = json.loads(line_body.lstrip())
        except (TypeError, ValueError):
            logger.error(
                "MCP SSE frame was not valid JSON request_id=%s detail=%s",
                request_id,
                line_body,
            )
            payload = _safe_error_payload(
                "request_failed",
                "The MCP request failed; check server logs.",
                request_id,
            )
        else:
            payload = _sanitize_json_rpc_payload(payload, request_id)
        sanitized_lines.append(f"data: {json.dumps(payload)}{newline}")

    return "".join(sanitized_lines).encode("utf-8")


async def _send_json(send, status: int, body: bytes, *, request_id: str | None = None) -> None:
    headers = [(b"content-type", b"application/json")]
    if request_id:
        headers.append((b"x-request-id", request_id.encode("ascii")))
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": headers,
        }
    )
    await send({"type": "http.response.body", "body": body})


def build_http_app(
    server=None,
    *,
    token: str | None = None,
    auth_store=None,
    config=None,
    json_response: bool = True,
) -> StatelessMCPApp:
    """Build the ASGI app: the MCP server behind a stateless HTTP session manager.

    Args:
        server: A configured MCP ``Server``; built via :func:`create_mcp_server`
            when omitted (separated for tests, which inject a scoped Memory).
        token: Optional bearer token; when set, every request must carry
            ``Authorization: Bearer <token>`` when no ``auth_store`` is
            supplied. Production deployments should supply ``auth_store`` so
            the bearer is a per-request PAT principal.
        auth_store: Optional ``AuthStore`` used to authenticate ``llmm_`` PATs.
        config: Optional server config. If authentication is enabled and no
            auth store is supplied, requests fail closed.
        json_response: Plain JSON responses instead of SSE frames. JSON is the
            default because a stateless server has no stream worth holding open.
    """
    if not MCP_AVAILABLE:
        raise ImportError("MCP package not installed. Install with: pip install visp-memory[mcp]")

    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

    if server is None:
        server = create_mcp_server()
    if config is None:
        memory = getattr(server, "_visp_memory", None)
        config = getattr(memory, "config", None)
    if auth_store is None and config is not None and config.server.auth_enabled:
        try:
            from visp_memory.server.auth_store import AuthStore
        except ImportError as error:
            raise ImportError(
                "PAT authentication requires the API dependencies. "
                "Install with: pip install visp-memory[api,mcp]"
            ) from error
        auth_store = AuthStore(config.storage.data_dir / "auth.db")

    manager = StreamableHTTPSessionManager(
        app=server,
        event_store=None,
        json_response=json_response,
        stateless=True,
    )
    return StatelessMCPApp(
        manager, token=token, auth_store=auth_store, config=config
    )


def _bearer_token(scope) -> str | None:
    """Extract a bearer credential without accepting malformed header values."""
    header = next(
        (
            value
            for name, value in scope.get("headers", [])
            if name.lower() == b"authorization"
        ),
        b"",
    )
    prefix = b"Bearer "
    if not header.startswith(prefix):
        return None
    try:
        token = header[len(prefix) :].decode("ascii")
    except UnicodeDecodeError:
        return None
    return token or None


def _authorized(scope, token: str) -> bool:
    """Constant-time check of the Authorization header against the bearer token."""
    header = next(
        (value for name, value in scope.get("headers", []) if name == b"authorization"),
        b"",
    )
    expected = f"Bearer {token}".encode()
    return hmac.compare_digest(header, expected)


def main() -> int:
    """Entry point for the stateless HTTP MCP server."""
    logging.basicConfig(level=logging.INFO)
    if not MCP_AVAILABLE:
        print("Error: MCP package not installed.")
        print("Install with: pip install visp-memory[mcp]")
        return 1

    host = os.environ.get("VISP_MEMORY_MCP_HTTP_HOST", DEFAULT_HOST).strip() or DEFAULT_HOST
    port = int(os.environ.get("VISP_MEMORY_MCP_HTTP_PORT", str(DEFAULT_PORT)))
    token = os.environ.get("VISP_MEMORY_MCP_HTTP_TOKEN") or None

    if token is None and host not in LOOPBACK_HOSTS:
        print(
            f"Refused: binding {host} without a token exposes the memory write surface "
            "to the network. Set VISP_MEMORY_MCP_HTTP_TOKEN, or bind a loopback address "
            "and put an authenticating proxy in front."
        )
        return 1

    import uvicorn

    from visp_memory.config import load_config

    config = load_config()
    auth_store = None
    if config.server.auth_enabled:
        try:
            from visp_memory.server.auth_store import AuthStore

            auth_store = AuthStore(config.storage.data_dir / "auth.db")
        except ImportError:
            print(
                "Error: PAT authentication requires the API dependencies. "
                "Install with: pip install visp-memory[api,mcp]"
            )
            return 1

    logger.info("Visp Memory stateless MCP server on http://%s:%d/mcp", host, port)
    uvicorn.run(
        build_http_app(
            token=token,
            auth_store=auth_store,
            config=config,
        ),
        host=host,
        port=port,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
