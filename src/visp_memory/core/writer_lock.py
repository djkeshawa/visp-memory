"""Keep served stores separate from local processes using crash-released OS locks."""

import errno
import json
import logging
import os
import threading
import time
import uuid
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)
_mutex = threading.RLock()
_registry = {}


class WriterLockConflict(RuntimeError):  # noqa: N818 - public conflict name
    """A live process owns an incompatible role for this store."""

    def __init__(self, data_dir: Path, local_pids=()):
        # Kept so a caller can word its refusal by holder: local writers, not a server.
        self.local_pids = tuple(sorted(set(local_pids)))
        if local_pids:
            detail = (
                f"live local writers (pids: {', '.join(self.local_pids)})"
                "; stop those processes before serving or maintaining the store"
            )
        else:
            detail = _holder_detail(read_server_metadata(data_dir))
        super().__init__(f"Storage writer conflict for {data_dir}: {detail}")


def read_server_metadata(data_dir: Path) -> dict:
    """What the current server.lock holder recorded about itself; empty if unreadable."""
    with suppress(OSError, ValueError):
        value = json.loads((Path(data_dir) / ".locks/server.json").read_text())
        if isinstance(value, dict):
            return value
    return {}


def _holder_detail(metadata):
    """Word the conflict by what holds server.lock: a server or offline maintenance.

    Maintenance writes metadata with no url, so a url is what marks a server.
    """
    pid = metadata.get("pid", "unknown")
    url = metadata.get("url")
    client_hint = (
        "set `storage.mode: client` and `storage.server_url: {}` in visp-memory.yaml "
        "(or run `visp-memory connect`)"
    )
    if url:
        return f"a server (pid: {pid}) is serving this store at {url}; {client_hint.format(url)}"
    if metadata.get("pid") is not None:
        return (
            f"an offline maintenance command (pid: {pid}) is using this store; "
            "wait for it to finish and retry"
        )
    return (
        "another process holds the server role (pid: unknown); if it is a server, "
        + client_hint.format("<server-url>")
        + ", otherwise wait for it to finish"
    )


@dataclass
class _HeldLock:
    path: Path
    fd: int
    references: int = 1


class WriterLockHandle:
    """Each handle releases only its own reference, including when discarded."""

    def __init__(self, key=None, held=None):
        self._key = key
        self._held = held

    def release(self):
        with _mutex:
            held, self._held = self._held, None
            if held is None or _registry.get(self._key) is not held:
                return
            held.references -= 1
            if held.references:
                return
            del _registry[self._key]
            if self._key[1] == "server":
                with suppress(OSError):
                    held.path.with_suffix(".json").unlink()
            os.close(held.fd)
            if self._key[1] == "local":
                with suppress(OSError):
                    held.path.unlink()  # Windows requires closing before unlinking.

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.release()

    def __del__(self):
        self.release()


def _try_lock(fd):
    try:
        if os.name == "nt":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError as error:
        if error.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
            return False
        raise


@contextmanager
def exclusive_file_lock(path: Path):
    """Serialize runtime record changes; keep the lock inode stable across cleanup."""
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if not _try_lock(fd):
            raise RuntimeError(f"Another server is updating runtime records at {path}")
        yield
    finally:
        os.close(fd)


def server_role_is_held(data_dir: Path) -> bool:
    """Probe the OS lock without creating files or trusting stale server metadata."""
    data_dir = Path(data_dir).resolve()
    with _mutex:
        if (data_dir, "server") in _registry:
            return True
        try:
            fd = _open_locked_existing(data_dir)
        except FileNotFoundError:
            return False
        except WriterLockConflict:
            return True
        except OSError:
            return True  # An unavailable probe cannot certify safe migration.
        os.close(fd)
        return False


def _open_locked_existing(data_dir):
    fd = os.open(data_dir / ".locks/server.lock", os.O_RDWR)
    try:
        for pause in (0, *_GATE_PAUSES):
            if pause:
                time.sleep(pause)
            if _try_lock(fd):
                return fd
        raise WriterLockConflict(data_dir)
    except BaseException:
        os.close(fd)
        raise


# Local writers take server.lock only as a momentary gate, so a busy gate is not
# evidence of a server until it stays busy. A real server holds it far longer.
# A loaded machine can deschedule a local writer mid-gate: Windows CI outlasted a
# flat 50 ms budget. The pauses grow from 5 ms to a 50 ms cap, about 0.46 s in
# all, so a real server is still reported well within a second.
_GATE_PAUSES = tuple(min(0.005 * 1.3**step, 0.05) for step in range(15))


def _open_locked(path, data_dir):
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        for pause in (0, *(_GATE_PAUSES if path.name == "server.lock" else ())):
            if pause:
                time.sleep(pause)
            if _try_lock(fd):
                return fd
        raise WriterLockConflict(data_dir)
    except BaseException:
        os.close(fd)
        raise


def _local_is_live(path):
    try:
        fd = os.open(path, os.O_RDWR)
        try:
            if not _try_lock(fd):
                return True
        finally:
            os.close(fd)
        path.unlink()
    except FileNotFoundError:
        pass  # A local process finished while we scanned.
    except PermissionError:
        return True
    return False


def _check_locals(data_dir):
    own = _registry.get((data_dir, "local"))
    live = []
    try:
        with os.scandir(data_dir / ".locks") as entries:
            for entry in entries:
                if not (entry.name.startswith("local-") and entry.name.endswith(".lock")):
                    continue
                path = Path(entry.path)
                if (own is None or own.path != path) and _local_is_live(path):
                    live.append(entry.name.split("-", 2)[1])
    except OSError:
        if not live:
            raise
    if live:
        raise WriterLockConflict(data_dir, live)


def _write_metadata(data_dir, url):
    try:
        (data_dir / ".locks/server.json").write_text(json.dumps({
            "pid": os.getpid(), "ppid": os.getppid(), "url": url,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }), encoding="utf-8")
    except OSError as error:
        logger.warning("Cannot write server metadata for %s: %s", data_dir, error)


def _acquire(data_dir, role, url):
    locks = data_dir / ".locks"
    locks.mkdir(parents=True, exist_ok=True)
    path = locks / (
        f"local-{os.getpid()}-{uuid.uuid4().hex}.lock" if role == "local" else "server.lock"
    )
    fd = _open_locked(path, data_dir)
    try:
        if role == "server":
            _check_locals(data_dir)
            _write_metadata(data_dir, url)
        elif (data_dir, "server") not in _registry:
            server_fd = _open_locked(locks / "server.lock", data_dir)
            try:
                # A server can scan between our create and lock. Repair its stale
                # cleanup while holding the gate, before allowing another scan.
                if not path.exists():
                    os.close(fd)
                    fd = None
                    fd = _open_locked(path, data_dir)
            finally:
                os.close(server_fd)
        return _HeldLock(path, fd)
    except BaseException:
        if fd is not None:
            os.close(fd)
        if role == "local":
            with suppress(OSError):
                path.unlink()
        raise


def acquire_writer_lock(data_dir: Path, role: str = "local", *, url: str | None = None):
    """Announce before checking the other role so concurrent starts cannot both win."""
    if role not in {"local", "server"}:
        raise ValueError(f"Unknown storage writer role: {role}")
    if os.environ.get("VISP_MEMORY_STORAGE_WRITER_GUARD", "").lower() == "off":
        return WriterLockHandle()
    try:
        data_dir = Path(data_dir).resolve()
        with _mutex:
            key = (data_dir, role)
            if key in _registry:
                held = _registry[key]
                held.references += 1
            else:
                held = _registry[key] = _acquire(data_dir, role, url)
            return WriterLockHandle(key, held)
    except OSError as error:
        logger.warning(
            "Storage writer guard unavailable for %s; continuing unguarded: %s", data_dir, error
        )
        return WriterLockHandle()


def _after_fork():
    global _mutex
    # Closing inherited descriptors must not explicitly unlock the parent's flock.
    for held in _registry.values():
        os.close(held.fd)
    _registry.clear()
    _mutex = threading.RLock()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork)
