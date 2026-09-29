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
    except WriterLockConflict:
        # A diagnostic only. Nothing here may act on the parent: a shared parent is
        # also what two unrelated servers started from one shell look like.
        sibling = _holder_is_sibling(config.storage.data_dir)
        logger.error(_SIBLING_WORKER if sibling else _ONE_PROCESS)
        raise


def preflight_server_role(config) -> None:
    """Refuse at import while another process owns the store, before touching it.

    Storage is still built at import, and building it migrates the schema and
    bootstraps accounts. Claiming and releasing the role here keeps those writes
    from racing a live server or an offline upgrade, without letting the importing
    supervisor keep a lock that belongs to the serving process. This is a fail-fast
    check; the lifespan claim is what excludes other processes.
    """
    _acquire_or_explain(config).release()


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
