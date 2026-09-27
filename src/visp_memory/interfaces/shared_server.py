import os
from pathlib import Path

import typer

from visp_memory.config import MemoryConfig, load_config
from visp_memory.core.paths import shared_root

# Written only when the shared root has no config yet. Its job is to exist: every
# request's load_config() then reads this file instead of walking up from the
# server's working directory, where a project's visp-memory.yaml would otherwise
# leak its repo_id, provider and storage settings into the shared server.
DEFAULT_SHARED_CONFIG = "storage:\n  data_dir: data\n"


def configure_shared_server(data_dir: Path | None = None) -> MemoryConfig:
    """Prepare inherited settings for one project-neutral local server."""
    root = shared_root().expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    config_path = root / "config.yaml"
    if not config_path.exists():
        config_path.write_text(DEFAULT_SHARED_CONFIG, encoding="utf-8")
    os.environ["VISP_MEMORY_CONFIG"] = str(config_path)

    effective_data_dir = (data_dir or root / "data").expanduser().resolve()
    effective_data_dir.mkdir(parents=True, exist_ok=True)
    os.environ["VISP_MEMORY_STORAGE_DATA_DIR"] = str(effective_data_dir)
    os.environ["VISP_MEMORY_SERVER_SHARED"] = "true"
    os.environ.pop("VISP_MEMORY_REPO_ID", None)

    config = load_config(root)
    if config.server.shared and config.repo_id:
        typer.echo(
            "Refusing to start a shared server with repo_id configured. "
            "Remove repo_id from the shared config; each request must name its project.",
            err=True,
        )
        raise typer.Exit(1)
    return config
