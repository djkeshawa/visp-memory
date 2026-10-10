"""One config selection order for loading and editing project settings."""

import os
from pathlib import Path

CONFIG_CANDIDATES = ("visp-memory.yaml", "visp-memory.json", ".visp-memory/config.yaml")


def shared_server_config() -> Path:
    """Return the shared server's own config file, which is never a project config."""
    # Imported lazily: visp_memory.core's package init imports config, which imports us.
    from visp_memory.core.paths import shared_root

    return (shared_root().expanduser() / "config.yaml").resolve()


def find_config_file(start_dir: Path | None = None) -> tuple[Path, Path] | None:
    """Return the active file and base for its relative storage paths."""
    explicit_path = os.environ.get("VISP_MEMORY_CONFIG")
    if explicit_path:
        path = Path(explicit_path).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"VISP_MEMORY_CONFIG points to a missing file: {path}")
        return path, path.parent

    start = Path(start_dir or Path.cwd()).resolve()
    # ~/.visp-memory/config.yaml belongs to the shared server, not to a project
    # rooted at $HOME; it is only ever selected explicitly via VISP_MEMORY_CONFIG.
    shared_config = shared_server_config()
    for parent in (start, *start.parents):
        for name in CONFIG_CANDIDATES:
            path = parent / name
            if path.exists() and path.resolve() != shared_config:
                # Discovered .visp-memory/config.yaml uses the project base;
                # explicit files above use their own containing directory.
                return path, parent
    return None
