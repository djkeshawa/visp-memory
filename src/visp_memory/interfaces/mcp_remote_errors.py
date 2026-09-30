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
    """Raised out of ``call_tool`` so the SDK marks the result ``isError``.

    Every mcp 1.x release turns an exception from the tool function into an
    error result carrying ``str(exception)`` as its text. Returning a
    ``CallToolResult`` would only work on newer releases than the declared floor.
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
