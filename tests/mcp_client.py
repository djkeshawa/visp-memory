"""Drive an MCP server in-process through the SDK client, as a real client would.

Each call opens its own connection so a test sees exactly one request's result;
the server object (and its Memory) is shared across calls.
"""

from typing import Any


async def call_tool(
    server, name: str, arguments: dict[str, Any] | None = None, *, client_info=None
):
    """Call one tool and return its ``CallToolResult``.

    ``client_info`` is the ``Implementation`` the client announces, which the
    server uses to label writes.
    """
    from mcp import Client

    async with Client(server, client_info=client_info) as client:
        return await client.call_tool(name, arguments or {})


async def list_tools(server) -> list:
    """Return the tools the server advertises."""
    from mcp import Client

    async with Client(server) as client:
        return (await client.list_tools()).tools
