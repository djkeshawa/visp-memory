"""Park only positively identified Uvicorn workers that cannot own the store."""

import logging
import multiprocessing
import os
import signal
import threading

SERVER_IMPORT_OWNER_PID_ENV = "VISP_MEMORY_SERVER_IMPORT_OWNER_PID"

logger = logging.getLogger(__name__)


def _uvicorn_spawn_config():
    """Read the spawn payload, never infer supervision from a shared parent pid."""
    process = multiprocessing.current_process()
    target = getattr(process, "_target", None)
    if (getattr(target, "__module__", None) != "uvicorn._subprocess"
            or getattr(target, "__name__", None) != "subprocess_started"):
        return None
    # Uvicorn exposes no app-side worker-mode hook. Check both payload type and
    # entry point; if its spawn contract changes, default to the ordinary refusal.
    from uvicorn import Config

    config = getattr(process, "_kwargs", {}).get("config")
    return config if isinstance(config, Config) else None


def is_uvicorn_multiworker() -> bool:
    config = _uvicorn_spawn_config()
    return config is not None and config.workers > 1 and not config.reload


def retain_serving_import_role() -> bool:
    """Keep import's role only for a known serving process, never a supervisor."""
    return (
        os.environ.get(SERVER_IMPORT_OWNER_PID_ENV) == str(os.getpid())
        or _uvicorn_spawn_config() is not None
    )


def idle_refused_worker() -> None:
    """Keep the supervisor heartbeat alive without serving or touching the parent."""
    logger.error(
        "Uvicorn --workers > 1 is unsupported. This refused worker will remain idle "
        "without serving to prevent repeated respawns. Restart with a single worker."
    )
    # Uvicorn's handler merely sets should_exit, which cannot stop an import that
    # is waiting. Replace it in this worker only, so supervisor shutdown still ends
    # the wait (SIGBREAK is translated to SIGTERM by Uvicorn on Windows).
    previous = signal.signal(signal.SIGTERM, _exit_worker)
    try:
        pause = threading.Event()
        while True:
            # A finite wait lets Windows dispatch console signals as well.
            pause.wait(0.5)
    finally:
        signal.signal(signal.SIGTERM, previous)


def _exit_worker(signum, frame):
    raise SystemExit(0)
