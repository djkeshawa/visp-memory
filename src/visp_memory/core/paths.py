from pathlib import Path


def shared_root() -> Path:
    """Return the per-user root for the one shared local server."""
    return Path.home() / ".visp-memory"

