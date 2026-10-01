"""Read the local-owner token only for requests to a loopback server."""

import json
from ipaddress import IPv6Address, ip_address
from pathlib import Path
from urllib.parse import urlsplit

from visp_memory.core.owner_token import OWNER_TOKEN_HEADER, owner_token_paths
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
    return parsed.scheme, host, port


def owner_token_path_for_request(server_url: str, request_url: str) -> Path | None:
    """Select proof only when discovery records this exact loopback origin."""
    origin = _origin(server_url)
    if origin is None or origin != _origin(request_url):
        return None
    directory = run_dir()
    token, record = owner_token_paths(directory, origin[1], origin[2])
    if record.exists():
        return token if _record_matches(record, origin, require_host=True) else None
    # Old servers recorded their literal bind in url, even though tokens used ports.
    legacy = directory / f"server-{origin[2]}.json"
    if _record_matches(legacy, origin, require_host=False):
        return directory / f"owner-{origin[2]}.token"
    return None


def _record_matches(path: Path, origin: tuple, *, require_host: bool) -> bool:
    try:
        metadata = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(metadata, dict) or not isinstance(metadata.get("url"), str):
            return False
        host = metadata.get("bind_host")
        return (
            _origin(metadata["url"]) == origin
            and (host == origin[1] or (not require_host and host is None))
        )
    except (OSError, ValueError):
        return False


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
    current_path = owner_token_path_for_request(url, url)
    current = read_owner_token(token_path) if current_path == token_path else None
    if current == sent_token:
        return None
    headers = dict(kwargs.get("headers") or {})
    if current:
        headers[OWNER_TOKEN_HEADER] = current
    else:
        headers.pop(OWNER_TOKEN_HEADER, None)
    return send(method, url, **{**kwargs, "headers": headers})
