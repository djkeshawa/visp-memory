"""Name the two client-mode failures an agent can act on.

In client mode every tool goes through the memory server. When that server is
not running, or refuses the request, "check server logs" points at logs the
agent cannot read. These two cases carry what is needed to fix them instead;
anything else stays on the generic, detail-free path.
"""

from __future__ import annotations

from typing import Any

from visp_memory.core.remote.errors import RemoteStorageError

START_HINT = "start it with `visp-memory serve --shared`"


class MCPToolError(Exception):
    """Raised out of ``call_tool``; the server answers with an ``isError`` result.

    mcp 2.x turns an exception escaping a handler into a protocol error, which a
    client shows as a failed request rather than a tool result the agent can read,
    so ``on_call_tool`` catches this and returns ``str(exception)`` as error text.
    """


def classify_remote_failure(error: RemoteStorageError) -> tuple[str, str, dict[str, Any]] | None:
    """Return (code, message, extra fields) for an actionable failure, else None."""
    url = error.server_url or "the configured server_url"
    if error.unreachable:
        return (
            "server_unavailable",
            f"The memory server at {url} is not reachable; {START_HINT}.",
            {"server_url": error.server_url},
        )
    status = error.status_code
    if status is not None and 400 <= status < 500:
        reason = error.detail or f"HTTP {status}"
        return (
            "server_rejected",
            f"The memory server at {url} rejected the request (HTTP {status}): {reason}",
            {"server_url": error.server_url, "status_code": status, "detail": error.detail},
        )
    return None
