"""Park only positively identified Uvicorn workers that cannot own the store."""

import logging
import multiprocessing
import os
import signal
import threading

from visp_memory.core.process_liveness import pid_is_alive

SERVER_IMPORT_OWNER_PID_ENV = "VISP_MEMORY_SERVER_IMPORT_OWNER_PID"

logger = logging.getLogger(__name__)


def _uvicorn_spawn_config():
    """Read the spawn payload, never infer supervision from a shared parent pid."""
    try:
        from uvicorn import Config
        from uvicorn._subprocess import subprocess_started
    except ImportError:
        return None

    process = multiprocessing.current_process()
    # Both 0.47 and 0.54 use this outer bootstrap, despite changing Process's
    # inner target. Identity avoids accepting an unrelated namesake launcher.
    if getattr(process, "_target", None) is not subprocess_started:
        return None

    # Unknown payloads retain ordinary refusal/release rather than parking or
    # retaining a supervisor's import role. Uvicorn exposes no app-side hook.
    kwargs = getattr(process, "_kwargs", None)
    if not isinstance(kwargs, dict):
        return None
    config = kwargs.get("config")
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
    # is waiting, so this worker installs its own. On Windows the supervisor sends
    # CTRL_BREAK; Uvicorn translates SIGBREAK to SIGTERM only once its server runs,
    # which a worker parked during import never reaches, so handle SIGBREAK too.
    stop_signals = [signal.SIGTERM]
    if hasattr(signal, "SIGBREAK"):
        stop_signals.append(signal.SIGBREAK)
    previous = {signum: signal.signal(signum, _exit_worker) for signum in stop_signals}
    supervisor = os.getppid()
    try:
        pause = threading.Event()
        while True:
            # A finite wait lets Windows dispatch console signals as well.
            pause.wait(0.5)
            # Never outlive the supervisor: if it is gone, nothing will stop us.
            if not pid_is_alive(supervisor):
                raise SystemExit(0)
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def _exit_worker(signum, frame):
    raise SystemExit(0)
