"""Stateless streamable-HTTP transport for the Visp Memory MCP server.

One process serves every client. Each POST to ``/mcp`` is a self-contained
MCP request: no session is created, no session id is issued or required, and
two consecutive requests share nothing but the durable store. That is what
makes the deployment horizontally scalable — any replica can answer any
request — and it is possible because the tool surface was already stateless:
every tool takes ``repo_id`` per call, unscoped writes are refused with the
repair named, and all durable state lives in storage.

Usage:
    visp-memory-mcp-http

Configuration (environment):
    VISP_MEMORY_MCP_HTTP_HOST   Bind address (default 127.0.0.1).
    VISP_MEMORY_MCP_HTTP_PORT   Port (default 8848).
    VISP_MEMORY_MCP_HTTP_TOKEN  Optional bearer token. Required to bind a
                                non-loopback address: this transport exposes
                                the write surface, and an open write surface
                                on a network interface is not a default anyone
                                chose on purpose.

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
import logging
import os

from visp_memory.interfaces.mcp import MCP_AVAILABLE, create_mcp_server

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

    def __init__(self, manager, token: str | None = None):
        self._manager = manager
        self._token = token

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
        if scope.get("path", "").rstrip("/") != "/mcp":
            await _send_json(send, 404, b'{"error": "not found; the MCP endpoint is /mcp"}')
            return
        if self._token is not None and not _authorized(scope, self._token):
            await _send_json(send, 401, b'{"error": "unauthorized"}')
            return
        await self._manager.handle_request(scope, receive, send)

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


async def _send_json(send, status: int, body: bytes) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send({"type": "http.response.body", "body": body})


def build_http_app(
    server=None, *, token: str | None = None, json_response: bool = True
) -> StatelessMCPApp:
    """Build the ASGI app: the MCP server behind a stateless HTTP session manager.

    Args:
        server: A configured MCP ``Server``; built via :func:`create_mcp_server`
            when omitted (separated for tests, which inject a scoped Memory).
        token: Optional bearer token; when set, every request must carry
            ``Authorization: Bearer <token>``.
        json_response: Plain JSON responses instead of SSE frames. JSON is the
            default because a stateless server has no stream worth holding open.
    """
    if not MCP_AVAILABLE:
        raise ImportError("MCP package not installed. Install with: pip install visp-memory[mcp]")

    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

    if server is None:
        server = create_mcp_server()

    manager = StreamableHTTPSessionManager(
        app=server,
        event_store=None,
        json_response=json_response,
        stateless=True,
    )
    return StatelessMCPApp(manager, token=token)


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

    logger.info("Visp Memory stateless MCP server on http://%s:%d/mcp", host, port)
    uvicorn.run(build_http_app(token=token), host=host, port=port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
