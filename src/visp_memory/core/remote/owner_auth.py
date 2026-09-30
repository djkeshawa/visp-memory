"""Read the local-owner token only for requests to a loopback server."""

from ipaddress import IPv6Address, ip_address
from pathlib import Path
from urllib.parse import urlsplit

from visp_memory.core.owner_token import OWNER_TOKEN_HEADER
from visp_memory.core.paths import run_dir

__all__ = [
    "OWNER_TOKEN_HEADER",
    "owner_token_path_for_request",
    "read_owner_token",
    "resend_with_rotated_token",
]


def _is_loopback(host: str | None) -> bool:
    if not host:
        return False
    if host.casefold().rstrip(".") == "localhost":
        return True
    try:
        address = ip_address(host)
    except ValueError:
        return False
    if isinstance(address, IPv6Address) and address.ipv4_mapped:
        return address.ipv4_mapped.is_loopback
    return address.is_loopback


def _origin(url: str) -> tuple[str, str, int] | None:
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        if parsed.scheme not in {"http", "https"} or not _is_loopback(host):
            return None
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return None
    return parsed.scheme, host.casefold().rstrip("."), port


def owner_token_path_for_request(server_url: str, request_url: str) -> Path | None:
    """Return the per-port proof path for a request to this loopback origin."""
    server_origin = _origin(server_url)
    if server_origin is None or server_origin != _origin(request_url):
        return None
    return run_dir() / f"owner-{server_origin[2]}.token"


def read_owner_token(path: Path) -> str | None:
    """Read a token on demand so clients follow a clean server restart."""
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return token or None


def resend_with_rotated_token(
    send, method: str, url: str, kwargs: dict, token_path: Path, sent_token: str
):
    """Resend a refused owner request once, and only if the token on disk changed.

    A changed token means the server restarted and minted new proof. An unchanged
    one means the refusal is real: resending cannot change the answer and would
    send every legitimately refused write twice. Returns None when not resent.
    """
    current = read_owner_token(token_path)
    if current == sent_token:
        return None
    headers = dict(kwargs.get("headers") or {})
    if current:
        headers[OWNER_TOKEN_HEADER] = current
    else:
        headers.pop(OWNER_TOKEN_HEADER, None)
    return send(method, url, **{**kwargs, "headers": headers})
