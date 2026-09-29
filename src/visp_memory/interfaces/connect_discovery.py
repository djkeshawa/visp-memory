"""Discover a live per-user shared server from its runtime records."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

import requests

from visp_memory.core.paths import run_dir
from visp_memory.core.process_liveness import pid_is_alive
from visp_memory.interfaces.connect_models import ConnectError, ServerRecord


def normalize_server_url(url: str) -> str:
    normalized = str(url).strip().rstrip("/")
    if not normalized:
        raise ConnectError("Server URL cannot be empty")
    return normalized


def probe_server(url: str) -> bool:
    try:
        response = requests.get(
            f"{normalize_server_url(url)}/",
            headers={"Accept": "application/json"},
            timeout=1.0,
        )
    except requests.RequestException:
        return False
    # A shared server requires repo_id on this endpoint, so 400 still proves
    # that the expected HTTP process answered. The full health check is scoped.
    return response.status_code < 500


def _read_server_record(path: Path) -> ServerRecord | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("url"), str):
            return None
        raw_data_dir = payload.get("data_dir")
        data_dir = (
            Path(raw_data_dir).expanduser().resolve()
            if isinstance(raw_data_dir, str) and raw_data_dir.strip()
            else None
        )
        pid = payload.get("pid")
        return ServerRecord(
            path=path,
            pid=pid if isinstance(pid, int) else None,
            url=normalize_server_url(payload["url"]),
            data_dir=data_dir,
        )
    except (OSError, ValueError, TypeError):
        return None


def server_records(directory: Path | None = None) -> list[ServerRecord]:
    directory = Path(directory or run_dir()).expanduser()
    try:
        paths = sorted(
            directory.glob("server-*.json"),
            key=lambda path: (path.stat().st_mtime_ns, path.name),
            reverse=True,
        )
    except OSError:
        return []
    records = []
    for path in paths:
        record = _read_server_record(path)
        if record is not None and pid_is_alive(record.pid):
            records.append(record)
    return records


def discover_shared_server(
    directory: Path | None = None,
    *,
    probe: Callable[[str], bool] | None = None,
) -> ServerRecord | None:
    """Return the newest runtime record whose process and HTTP endpoint are live."""
    probe = probe or probe_server
    for record in server_records(directory):
        if probe(record.url):
            return record
    return None


def matching_server_record(url: str, directory: Path | None = None) -> ServerRecord | None:
    normalized = normalize_server_url(url)
    return next(
        (record for record in server_records(directory) if record.url == normalized),
        None,
    )
