"""Which Host names an unauthenticated server answers to.

A server with no authentication -- auth disabled, or the loopback local-owner
view -- trusts whoever reaches it, so it must not be reachable through a name a
hostile web page controls. DNS rebinding points such a name at 127.0.0.1; the
browser then sends the page's Host and Origin, which is what this allowlist
refuses. Loopback names are always allowed; ``VISP_MEMORY_SERVER_ALLOWED_HOSTS``
adds others, and ``*`` there turns the check off for an operator who has put the
server behind something else that does it.

No FastAPI or Starlette imports: the MCP HTTP transport uses this too.
"""

import re
from typing import Iterable

LOOPBACK_HOSTNAMES = ("localhost", "127.0.0.1", "[::1]")
ANY_HOST = "*"

# A strict allowlist, not a parser: anything odd (userinfo, lists, trailing dots)
# fails to match and is refused instead of being interpreted.
_AUTHORITY = re.compile(r"([a-z0-9.\-]+|\[[0-9a-f:.]+\])(?::(\d{1,5}))?", re.IGNORECASE)


def _hostname(authority: str) -> str | None:
    match = _AUTHORITY.fullmatch(authority.strip())
    return match.group(1).lower() if match else None


def allows_any_host(extra_hosts: Iterable[str]) -> bool:
    return ANY_HOST in {host.strip() for host in extra_hosts}


def is_allowed_authority(authority: str, extra_hosts: Iterable[str] = ()) -> bool:
    """Whether ``host[:port]`` names this server: loopback, or a configured host."""
    hostname = _hostname(authority)
    if hostname is None:
        return False
    if hostname in LOOPBACK_HOSTNAMES:
        return True
    configured = {host.strip().lower() for host in extra_hosts}
    return hostname in configured or authority.strip().lower() in configured


def transport_hosts(extra_hosts: Iterable[str] = ()) -> tuple[list[str], list[str]]:
    """``(allowed_hosts, allowed_origins)`` in the MCP SDK's pattern syntax."""
    names = [*LOOPBACK_HOSTNAMES, *(host.strip().lower() for host in extra_hosts if host.strip())]
    hosts = [pattern for name in names for pattern in (name, f"{name}:*")]
    origins = [
        pattern
        for name in names
        for scheme in ("http", "https")
        for pattern in (f"{scheme}://{name}", f"{scheme}://{name}:*")
    ]
    return hosts, origins
