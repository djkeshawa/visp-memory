"""Create and remove same-user proof for local-owner maintenance access."""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from visp_memory.core.clock import utc_now
from visp_memory.core.owner_token import OWNER_TOKEN_FILE_ENV, owner_token_paths
from visp_memory.core.paths import ensure_private_dir, run_dir
from visp_memory.core.process_liveness import pid_is_alive
from visp_memory.core.writer_lock import exclusive_file_lock

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
    port: int


def _atomic_write(path: Path, content: str) -> None:
    """Replace a file in place so readers never see a partial token or record."""
    ensure_private_dir(path.parent)
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
    bind_host: str | None = None,
) -> OwnerTokenFiles:
    """Write the local owner's secret and a non-secret server discovery record."""
    directory = (directory or run_dir()).expanduser().resolve()
    bind_host = bind_host if bind_host is not None else urlsplit(url).hostname
    if not bind_host:
        raise ValueError("Owner token requires an exact bind host")
    token_path, server_path = owner_token_paths(directory, bind_host, port)
    token = secrets.token_urlsafe(32)
    pid = os.getpid()
    started_at = utc_now().isoformat()
    metadata = {
        "pid": pid,
        "url": url,
        "bind_host": bind_host,
        "data_dir": str(Path(data_dir).expanduser().resolve()),
        "started_at": started_at,
    }

    ensure_private_dir(directory)
    with exclusive_file_lock(directory / f".owner-{port}.lock"):
        for record in (server_path, directory / f"server-{port}.json"):
            _refuse_live_record(record)
        _atomic_write(token_path, token)
        try:
            _atomic_write(server_path, json.dumps(metadata, sort_keys=True))
        except BaseException:
            token_path.unlink(missing_ok=True)
            raise
    return OwnerTokenFiles(token, token_path, server_path, pid, started_at, port)


def _refuse_live_record(path: Path) -> None:
    try:
        metadata = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return
    except ValueError as error:
        raise RuntimeError(f"Cannot safely replace unreadable server record {path}") from error
    if not isinstance(metadata, dict) or pid_is_alive(metadata.get("pid")):
        pid = metadata.get("pid", "unknown") if isinstance(metadata, dict) else "unknown"
        raise RuntimeError(
            f"Server runtime record {path} belongs to a live or unidentified process "
            f"(pid: {pid}); stop that server before starting another on this endpoint"
        )


def cleanup_owner_token_files(files: OwnerTokenFiles) -> None:
    """Best-effort cleanup without deleting files written by a later server."""
    try:
        with exclusive_file_lock(files.token_path.parent / f".owner-{files.port}.lock"):
            _cleanup_owned_files(files)
    except (OSError, RuntimeError):
        pass


def _cleanup_owned_files(files: OwnerTokenFiles) -> None:
    try:
        if files.token_path.read_text(encoding="utf-8") == files.token:
            files.token_path.unlink()
    except OSError:
        pass

    try:
        metadata = json.loads(files.server_path.read_text(encoding="utf-8"))
        if (isinstance(metadata, dict) and metadata.get("pid") == files.pid
                and metadata.get("started_at") == files.started_at):
            files.server_path.unlink()
    except (OSError, ValueError):
        pass
