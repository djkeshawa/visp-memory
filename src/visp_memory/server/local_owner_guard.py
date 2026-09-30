"""Refuse DNS-rebinding requests to a server that is in local-owner mode.

Local-owner mode grants the store's owner view to any request whose *peer* is
loopback. A hostile web page can make its own hostname resolve to 127.0.0.1, after
which the victim's browser sends it to this server from a loopback peer and treats
the answers as same-origin. The peer check cannot tell that request from the
owner's dashboard; the Host and Origin headers can, because the attacker does not
control what the browser writes into them.

So, only while the local-owner view is being granted:

* the Host must name this machine (``localhost``, ``127.0.0.1`` or ``[::1]``);
* a state-changing request that carries an Origin must come from a loopback page
  that is either this server's own dashboard or a configured CORS origin.

Requests with no Origin (the CLI, RemoteStorage, MCP clients) are not browsers and
are unaffected. Team and authenticated servers never enable the mode, and a request
from a non-loopback peer never had the owner view, so neither is touched.
"""

import re
from typing import Optional

from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from visp_memory.server import auth

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# A strict allowlist, not a parser: anything odd (userinfo, lists, trailing dots)
# fails to match and is refused instead of being interpreted.
_LOOPBACK_AUTHORITY = re.compile(r"(?:localhost|127\.0\.0\.1|\[::1\])(?::\d{1,5})?", re.IGNORECASE)
_ORIGIN = re.compile(r"(https?)://([^/?#\s]+)", re.IGNORECASE)


def _is_loopback_authority(value: str) -> bool:
    return _LOOPBACK_AUTHORITY.fullmatch(value) is not None


def _normalise_origin(origin: str) -> Optional[str]:
    """Lower-case ``scheme://authority`` with no path, or None if it is not one."""
    match = _ORIGIN.fullmatch(origin.strip().rstrip("/"))
    return f"{match.group(1)}://{match.group(2)}".lower() if match else None


def refusal_for(scope: Scope, config) -> Optional[tuple[int, str]]:
    """Return ``(status, message)`` when this request must be refused."""
    if not auth.is_local_owner_request(Request(scope), config):
        return None
    headers = Headers(scope=scope)
    host = headers.get("host")
    # A missing Host cannot come from a browser, so there is nothing to rebind.
    if host is not None and not _is_loopback_authority(host):
        return 421, "This server only answers to localhost, 127.0.0.1 and [::1]."
    origin = headers.get("origin")
    if origin is None or scope["method"] not in UNSAFE_METHODS:
        return None
    normalised = _normalise_origin(origin)
    if normalised is None or not _is_loopback_authority(normalised.split("://", 1)[1]):
        return 403, "Cross-origin writes are not allowed."
    allowed = {
        candidate
        for candidate in map(_normalise_origin, config.server.cors_origins)
        if candidate is not None
    }
    # A wildcard entry is deliberately not honoured: it would let any page served
    # from another loopback port write. The server's own origin is always allowed.
    own = f"{scope.get('scheme', 'http')}://{host}".lower() if host else None
    if normalised in allowed or normalised == own:
        return None
    return 403, "Cross-origin writes are not allowed."


class LocalOwnerGuardMiddleware:
    """Pure ASGI, so it runs before routing and before any dependency."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        # Resolved through `auth` on every request, like the authentication code,
        # so both always judge a request against the same configuration.
        refusal = refusal_for(scope, auth.load_config())
        if refusal is None:
            await self.app(scope, receive, send)
            return
        status_code, message = refusal
        await JSONResponse({"detail": message}, status_code=status_code)(scope, receive, send)
