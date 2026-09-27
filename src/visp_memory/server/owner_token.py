"""Create and remove same-user proof for local-owner maintenance access."""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

from visp_memory.core.clock import utc_now
from visp_memory.core.owner_token import OWNER_TOKEN_FILE_ENV
from visp_memory.core.paths import run_dir

__all__ = [
    "OWNER_TOKEN_FILE_ENV",
    "OwnerTokenFiles",
    "cleanup_owner_token_files",
    "create_owner_token_files",
]


@dataclass(frozen=True)
class OwnerTokenFiles:
    token: str = field(repr=False)
    token_path: Path
    server_path: Path
    pid: int
    started_at: str


def _atomic_write(path: Path, content: str) -> None:
    """Replace a file in place so readers never see a partial token or record."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def create_owner_token_files(
    *,
    port: int,
    url: str,
    data_dir: Path | str,
    directory: Path | None = None,
) -> OwnerTokenFiles:
    """Write the local owner's secret and a non-secret server discovery record."""
    directory = (directory or run_dir()).expanduser().resolve()
    token_path = directory / f"owner-{port}.token"
    server_path = directory / f"server-{port}.json"
    token = secrets.token_urlsafe(32)
    pid = os.getpid()
    started_at = utc_now().isoformat()
    metadata = {
        "pid": pid,
        "url": url,
        "data_dir": str(Path(data_dir).expanduser().resolve()),
        "started_at": started_at,
    }

    _atomic_write(token_path, token)
    try:
        _atomic_write(server_path, json.dumps(metadata, sort_keys=True))
    except BaseException:
        try:
            token_path.unlink()
        except OSError:
            pass
        raise
    return OwnerTokenFiles(token, token_path, server_path, pid, started_at)


def cleanup_owner_token_files(files: OwnerTokenFiles) -> None:
    """Best-effort cleanup without deleting files written by a later server."""
    try:
        if files.token_path.read_text(encoding="utf-8") == files.token:
            files.token_path.unlink()
    except OSError:
        pass

    try:
        metadata = json.loads(files.server_path.read_text(encoding="utf-8"))
        if metadata.get("pid") == files.pid and metadata.get("started_at") == files.started_at:
            files.server_path.unlink()
    except (OSError, ValueError):
        pass
