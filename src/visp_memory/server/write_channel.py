"""Assign REST provenance from the server's authenticated request context."""

from visp_memory.core.trust import WriteChannel
from visp_memory.server.auth import UserContext


def request_write_channel(
    user: UserContext, *, default: WriteChannel = WriteChannel.HTTP
) -> WriteChannel:
    # Auth computes this proof only for anonymous local-owner loopback requests.
    # Keep a distinct channel so assisted REST writes remain auditable beside MCP.
    return WriteChannel.LOCAL_OWNER if user.owner_maintenance else default
