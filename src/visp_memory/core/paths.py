import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def shared_root() -> Path:
    """Return the per-user root for the one shared local server."""
    return Path.home() / ".visp-memory"


def run_dir() -> Path:
    """Return the per-user directory for local server discovery files."""
    return shared_root() / "run"


def ensure_private_dir(path: Path) -> Path:
    """Create ``path`` and strip group/other access so only its owner can enter it.

    ``mkdir(mode=0o700)`` only shapes a directory it creates, and only its leaf, so a
    root left at 0775 by an earlier run (or by the umask) would keep exposing the
    SQLite files under it. Permissions are only ever narrowed, never widened, and a
    directory owned by someone else or on a filesystem without POSIX modes is left
    alone with a warning instead of failing the server. A no-op on Windows, where
    ACLs rather than mode bits govern access.
    """
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == "nt":
        return path
    try:
        status = path.stat()
        if status.st_uid != os.getuid():
            logger.warning("Not tightening permissions on %s: not owned by this user", path)
        elif status.st_mode & 0o077:
            path.chmod(status.st_mode & 0o700)
    except OSError as error:
        logger.warning("Could not make %s private: %s", path, error)
    return path
