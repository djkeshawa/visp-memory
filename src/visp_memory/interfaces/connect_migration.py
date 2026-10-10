"""Copy a project's local graph into an already running shared server."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Callable

from visp_memory import Memory, MemoryConfig
from visp_memory.config import StorageConfig
from visp_memory.core.remote.errors import RemoteStorageError
from visp_memory.core.remote_storage import RemoteStorage
from visp_memory.core.writer_lock import server_role_is_held
from visp_memory.interfaces.connect_discovery import normalize_server_url
from visp_memory.interfaces.connect_graph import record_ids, trust_counts
from visp_memory.interfaces.connect_migration_trust import downgrade_for_migration
from visp_memory.interfaces.connect_models import ConnectError, ServerRecord
from visp_memory.interfaces.connect_source import source_repository_ids


def local_data_dir(storage: dict, project_root: Path) -> Path:
    """Resolve the store local startup would open: the env override wins, like load_config."""
    configured = os.environ.get("VISP_MEMORY_STORAGE_DATA_DIR") or storage.get(
        "data_dir", ".visp-memory/data"
    )
    if not isinstance(configured, (str, Path)):
        raise ConnectError("storage.data_dir must be a filesystem path")
    path = Path(configured).expanduser()
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()


def _local_memory_config(
    storage_document: dict, project_root: Path, repo_id: str,
) -> tuple[MemoryConfig, str]:
    storage_values = {
        key: value
        for key, value in storage_document.items()
        if key in StorageConfig.model_fields
    }
    config = MemoryConfig(repo_id=repo_id, project_name=project_root.name)
    try:
        config.storage = StorageConfig(**storage_values)
        # Startup applies deployment env overrides (backend, data_dir, ...) on top
        # of the file; the migration source must be that same store.
        config.apply_env_overrides()
    except ValueError as error:
        raise ConnectError(f"Cannot open the existing local store: {error}") from error
    config.repo_id = repo_id
    storage = config.storage
    configured_mode = storage.mode
    storage.mode = "local"
    storage.data_dir = local_data_dir(storage_document, project_root)
    # Export does not need semantic inference, and must not contact a provider.
    config.embedding.provider = "noop"
    return config, configured_mode


def _refuse_other_repositories(memory: Memory, target: str) -> None:
    repo_ids = source_repository_ids(memory._storage)
    for repo_id in sorted(repo_ids - {target}):
        raise ConnectError(
            f"Local store has records for repository {repo_id!r}, "
            f"but the target is {target!r}. "
            f"Refusing migration and config changes. Pass `--repo {repo_id}` to use that ID; "
            "for a store with multiple repositories, export/import each repository separately."
        )


def _report_source(graph: dict, source: Path, report: Callable[[str], None]) -> None:
    counts = trust_counts(graph)
    report(f"Local migration source: {source}")
    report("Trust tiers: " + ", ".join(f"{tier}: {count}" for tier, count in counts.items()))


def _data_dir_claims_server(data_dir: Path, server_url: str) -> bool:
    if not server_role_is_held(data_dir):
        return False
    metadata_path = data_dir / ".locks" / "server.json"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    recorded_url = metadata.get("url") if isinstance(metadata, dict) else None
    return (
        isinstance(recorded_url, str)
        and bool(recorded_url.strip())
        and normalize_server_url(recorded_url) == normalize_server_url(server_url)
    )


def refuse_served_source(
    source: Path,
    server_url: str,
    server_record: ServerRecord | None,
) -> None:
    same_recorded_dir = (
        server_record is not None
        and server_record.data_dir is not None
        and source == server_record.data_dir
        and server_role_is_held(source)
    )
    if same_recorded_dir or _data_dir_claims_server(source, server_url):
        raise ConnectError(
            f"Refusing to migrate {source}: it is the served data directory. "
            "The server already owns this store; connect the project without "
            "--migrate-local."
        )


def prepare_local_records(
    storage_document: dict,
    project_root: Path,
    repo_id: str,
    *,
    report: Callable[[str], None],
) -> tuple[dict, Path]:
    config, configured_mode = _local_memory_config(storage_document, project_root, repo_id)
    source = config.storage.data_dir
    try:
        populated = config.storage.backend != "sqlite" or (
            source.is_dir() and any(source.iterdir())
        )
    except OSError as error:
        raise ConnectError(f"Cannot inspect local data directory {source}: {error}") from error
    if not populated:
        if configured_mode != "client":
            # The project still reads a local store; switching it to client mode
            # after importing nothing would strand whatever it actually used.
            raise ConnectError(
                f"No local store to migrate at {source} (missing or empty). "
                "Refusing migration and config changes. Check storage.data_dir or "
                "VISP_MEMORY_STORAGE_DATA_DIR, or connect without --migrate-local."
            )
        graph = {}
        _report_source(graph, source, report)
        return graph, source

    memory = None
    try:
        memory = Memory(config=config)
        _refuse_other_repositories(memory, repo_id)
        graph = memory.export()
    except ConnectError:
        raise
    except Exception as error:
        raise ConnectError(f"Could not export local data from {source}: {error}") from error
    finally:
        if memory is not None:
            memory.close()

    _report_source(graph, source, report)
    return graph, source


def migrate_local_records(graph: dict, repo_id: str, remote: RemoteStorage) -> tuple[int, int]:
    identities = record_ids(graph)
    if not identities:
        return 0, 0
    try:
        existing = record_ids(remote.export_graph(repo_id=repo_id))
        # ``Memory.export(path)`` stringifies path-like config values when it
        # writes JSON. Do the equivalent in memory before requests serializes it.
        portable_graph = json.loads(json.dumps(downgrade_for_migration(graph), default=str))
        remote.import_graph(portable_graph, default_repo_id=repo_id)
    except (RemoteStorageError, ValueError) as error:
        raise ConnectError(f"Could not import local data through the server: {error}") from error
    return len(identities - existing), len(identities & existing)
