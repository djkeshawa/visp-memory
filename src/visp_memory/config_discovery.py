"""One config selection order for loading and editing project settings."""

import os
from pathlib import Path

CONFIG_CANDIDATES = ("visp-memory.yaml", "visp-memory.json", ".visp-memory/config.yaml")


def find_config_file(start_dir: Path | None = None) -> tuple[Path, Path] | None:
    """Return the active file and base for its relative storage paths."""
    explicit_path = os.environ.get("VISP_MEMORY_CONFIG")
    if explicit_path:
        path = Path(explicit_path).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"VISP_MEMORY_CONFIG points to a missing file: {path}")
        return path, path.parent

    start = Path(start_dir or Path.cwd()).resolve()
    for parent in (start, *start.parents):
        for name in CONFIG_CANDIDATES:
            path = parent / name
            if path.exists():
                # Discovered .visp-memory/config.yaml uses the project base;
                # explicit files above use their own containing directory.
                return path, parent
    return None
