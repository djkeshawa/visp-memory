"""Hold the server writer role in the process that serves, not the one that imports.

``uvicorn.run`` imports the app in its supervisor even with ``--reload`` or
``--workers``, then spawns the process that actually serves and imports it again.
A lock taken at import therefore belongs to the supervisor and locks the server
out of its own store. The lifespan runs only in the serving process, so that is
where the role is claimed.
"""

import logging
import os

from visp_memory.core.writer_lock import (
    WriterLockConflict,
    acquire_writer_lock,
    read_server_metadata,
)
from visp_memory.server.worker_mode import idle_refused_worker, is_uvicorn_multiworker

logger = logging.getLogger(__name__)

_ONE_PROCESS = (
    "The memory server runs as exactly one process per store. Run it with a single "
    "worker; a second server on the same data directory is refused."
)
_SIBLING_WORKER = (
    "This looks like a sibling worker of the server that already holds this store: "
    "`--workers` greater than 1 is not supported because one process owns the store. "
    "Restart with a single worker."
)
_LOCAL_WRITERS = (
    "Local processes (pids: {pids}) hold this store, such as a Claude Code or Codex "
    "stdio MCP session. Close them, or switch them to client mode with "
    "`visp-memory connect`, before serving."
)


def server_url(server_config) -> str:
    host = server_config.host
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"http://{host}:{server_config.port}"


def _acquire_or_explain(config):
    try:
        return acquire_writer_lock(
            config.storage.data_dir, "server", url=server_url(config.server)
        )
    except WriterLockConflict as conflict:
        logger.error(_explain(conflict, config.storage.data_dir))
        if is_uvicorn_multiworker():
            idle_refused_worker()
        raise


def _explain(conflict, data_dir) -> str:
    """Word the refusal by what holds the store: local writers, a sibling, or a server."""
    if conflict.local_pids:
        return _LOCAL_WRITERS.format(pids=", ".join(conflict.local_pids))
    # A diagnostic only. Nothing here may act on the parent: a shared parent is
    # also what two unrelated servers started from one shell look like.
    return _SIBLING_WORKER if _holder_is_sibling(data_dir) else _ONE_PROCESS


def preflight_server_role(config, hold=None) -> None:
    """Refuse at import while another process owns the store, before touching it.

    Storage is still built at import, which migrates the schema, and the stores
    beside it create auth.db and lifecycle.db and bootstrap the first account. Pass
    the ``ExitStack`` that spans those writes as ``hold`` and the role stays held
    until it closes, so they cannot race a live server, offline maintenance or a
    local writer. Supervisors close it at the end of import; known serving
    processes retain it for lifespan to adopt without a gap. Without ``hold``
    this is only a check. The lifespan claim holds the role while serving.
    """
    handle = _acquire_or_explain(config)
    if hold is None:
        handle.release()
    else:
        hold.callback(handle.release)


def claim_server_role(config):
    """Take the role for the serving process; the caller releases it on shutdown."""
    return _acquire_or_explain(config)


def _holder_is_sibling(data_dir) -> bool:
    """True when a server (not maintenance) was spawned by this process's parent."""
    holder = read_server_metadata(data_dir)
    return (
        bool(holder.get("url"))
        and holder.get("ppid") == os.getppid()
        and holder.get("pid") != os.getpid()
    )
