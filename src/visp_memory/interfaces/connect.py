"""Connect one project to the per-user shared Visp Memory server."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Iterable

import requests

from visp_memory.config_discovery import find_config_file
from visp_memory.core.paths import run_dir
from visp_memory.core.remote.errors import RemoteStorageError
from visp_memory.core.remote_storage import RemoteStorage
from visp_memory.core.storage import is_implicitly_registered
from visp_memory.interfaces.connect_config import load_document, write_client_config
from visp_memory.interfaces.connect_discovery import (
    discover_shared_server,
    matching_server_record,
    normalize_server_url,
)
from visp_memory.interfaces.connect_discovery import (
    probe_server as _probe_server,
)
from visp_memory.interfaces.connect_migration import (
    local_data_dir as resolve_local_data_dir,
)
from visp_memory.interfaces.connect_migration import (
    migrate_local_records,
    prepare_local_records,
    refuse_served_source,
)
from visp_memory.interfaces.connect_models import ConnectError, ConnectResult

DEFAULT_SERVER_URL = "http://127.0.0.1:8000"
START_SERVER_HINT = "start it with `visp-memory serve --shared`"
SUPPORTED_AGENT_CONFIGS = {"claude-code", "codex"}


def find_project_root(start: Path | None = None) -> Path:
    """Prefer the active config root, then Git's top level, then cwd."""
    start = Path(start or Path.cwd()).expanduser().resolve()
    selected = find_config_file(start)
    if selected is not None:
        return selected[1]

    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=start,
            check=True,
            capture_output=True,
            text=True,
        )
        top_level = completed.stdout.strip()
        if top_level:
            return Path(top_level).resolve()
    except (OSError, subprocess.CalledProcessError):
        pass
    return start


def _repo_id(document: dict, explicit: str | None, project_root: Path) -> str:
    candidate = explicit if explicit is not None else document.get("repo_id")
    if candidate is None:
        candidate = project_root.name
    if not isinstance(candidate, str) or not candidate.strip():
        raise ConnectError("Repository ID cannot be empty")
    return candidate.strip()


def _storage_document(document: dict) -> dict:
    storage = document.get("storage")
    if storage is None:
        return {}
    if not isinstance(storage, dict):
        raise ConnectError("Config field 'storage' must be an object")
    return storage


def _remote_storage(
    storage: dict, server_url: str, repo_id: str, *, credentials_allowed: bool = True,
) -> RemoteStorage:
    timeout = os.environ.get(
        "VISP_MEMORY_STORAGE_CONNECT_TIMEOUT_SECONDS",
        storage.get("connect_timeout_seconds", 15.0),
    )
    try:
        timeout = float(timeout)
    except (TypeError, ValueError) as error:
        raise ConnectError("storage.connect_timeout_seconds must be a number") from error
    return RemoteStorage(
        server_url=server_url,
        api_key=(os.environ.get("VISP_MEMORY_API_KEY") or storage.get("api_key"))
        if credentials_allowed else None,
        jwt_token=(os.environ.get("VISP_MEMORY_JWT_TOKEN") or storage.get("jwt_token"))
        if credentials_allowed else None,
        timeout=timeout,
        repo_id=repo_id,
    )


def _check_server(remote: RemoteStorage, server_url: str, repo_id: str) -> dict:
    try:
        root = remote.session.get(f"{server_url}/", params={"repo_id": repo_id})
        root.raise_for_status()
        capabilities = remote.session.get(f"{server_url}/diagnostics/capabilities")
        capabilities.raise_for_status()
        payload = capabilities.json()
        if not isinstance(payload, dict):
            raise ConnectError("server returned an invalid capabilities response")
        return payload
    except (requests.RequestException, RemoteStorageError, ValueError) as error:
        raise ConnectError(
            f"Cannot connect to Visp Memory server at {server_url}.\n"
            f"Please {START_SERVER_HINT}.\n{error}"
        ) from error


def _register_repository(remote: RemoteStorage, repo_id: str) -> bool:
    try:
        existing = remote.get_repository(repo_id)
        if existing is not None and not is_implicitly_registered(existing):
            return False
        remote.store_repository({"id": repo_id, "name": repo_id})
        return True
    except RemoteStorageError as error:
        # A concurrent connect may have registered the same repository after our
        # read. Accept that race only when the row now really exists.
        try:
            if remote.get_repository(repo_id) is not None:
                return False
        except RemoteStorageError:
            pass
        raise ConnectError(f"Could not register repository {repo_id!r}: {error}") from error


def install_agent_config(target: str, *, project_root: Path, repo_id: str) -> list[str]:
    """Run the same project integrations exposed by ``hooks install``."""
    memory = SimpleNamespace(config=SimpleNamespace(repo_id=repo_id))
    if target == "codex":
        from visp_memory.hooks.codex import CODEX_MIGRATION_NOTE, CodexAdapter

        adapter = CodexAdapter(memory, project_root=project_root)
        results = adapter.install()
        notes = [CODEX_MIGRATION_NOTE] if adapter.replaced_pinned_config else []
    elif target == "claude-code":
        from visp_memory.hooks.claude_code import ClaudeCodeAdapter
        from visp_memory.hooks.claude_code_auto import install_auto_inject_hooks

        adapter = ClaudeCodeAdapter(memory, project_root=project_root)
        results = adapter.install()
        results["claude_mcp_config"] = adapter.install_mcp_config()
        install_auto_inject_hooks(project_root)
        notes = []
    else:
        choices = ", ".join(sorted(SUPPORTED_AGENT_CONFIGS))
        raise ConnectError(f"Unknown agent config {target!r}; choose one of: {choices}")

    failed = [name for name, succeeded in results.items() if not succeeded]
    if failed:
        raise ConnectError(
            f"Could not install {target} integration: {', '.join(sorted(failed))}"
        )
    return notes


def connect_project(
    *,
    server_url: str | None = None,
    repo_id: str | None = None,
    migrate_local: bool = False,
    report: Callable[[str], None] = print,
    agent_configs: Iterable[str] = (),
    start_dir: Path | None = None,
) -> ConnectResult:
    """Validate a shared server, optionally migrate, then persist client mode."""
    project_root = find_project_root(start_dir)
    selected = find_config_file(start_dir)
    config_path = selected[0] if selected else project_root / "visp-memory.yaml"
    document = load_document(config_path)
    storage = _storage_document(document)
    resolved_repo_id = _repo_id(document, repo_id, project_root)
    runtime_directory = run_dir()

    discovered = None
    if server_url is None:
        discovered = discover_shared_server(runtime_directory, probe=_probe_server)
        resolved_url = discovered.url if discovered else DEFAULT_SERVER_URL
    else:
        resolved_url = normalize_server_url(server_url)
        discovered = matching_server_record(resolved_url, runtime_directory)

    requested_agents = tuple(
        target.value if hasattr(target, "value") else str(target)
        for target in agent_configs
    )
    unknown_agents = sorted(set(requested_agents) - SUPPORTED_AGENT_CONFIGS)
    if unknown_agents:
        choices = ", ".join(sorted(SUPPORTED_AGENT_CONFIGS))
        raise ConnectError(
            f"Unknown agent config {unknown_agents[0]!r}; choose one of: {choices}"
        )

    configured_url = os.environ.get("VISP_MEMORY_STORAGE_SERVER_URL") or storage.get("server_url")
    credentials_allowed = not (server_url is None and discovered is not None) or (
        isinstance(configured_url, str)
        and normalize_server_url(configured_url) == resolved_url
    )
    remote = _remote_storage(
        storage, resolved_url, resolved_repo_id, credentials_allowed=credentials_allowed,
    )
    local_data_dir = None
    migrated_records = 0
    already_present_records = 0
    notes: list[str] = []
    try:
        capabilities = _check_server(remote, resolved_url, resolved_repo_id)
        if not capabilities.get("repositories", False):
            raise ConnectError("The server storage backend cannot register repositories")

        if migrate_local:
            local_data_dir = resolve_local_data_dir(storage, project_root)
            refuse_served_source(local_data_dir, resolved_url, discovered)
            graph, local_data_dir = prepare_local_records(
                storage, project_root, resolved_repo_id, report=report,
            )

        repository_registered = _register_repository(remote, resolved_repo_id)
        if migrate_local:
            migrated_records, already_present_records = migrate_local_records(
                graph, resolved_repo_id, remote,
            )
            report(
                "Memories and evidence imported as external; to re-approve memories after owner "
                "review, use PATCH /memories/{id} with source=authored and replace provenance:* "
                "tags with provenance:authored (keep other tags)."
            )
            if graph.get("authority_attestations"):
                report(
                    "Signed authority attestations retained: the downgrade leaves signed content, "
                    "scope and evidence hashes unchanged."
                )
        note = write_client_config(config_path, document, resolved_repo_id, resolved_url)
        if note:
            notes.append(note)

        for target in requested_agents:
            notes.extend(
                install_agent_config(
                    target,
                    project_root=project_root,
                    repo_id=resolved_repo_id,
                )
            )
    finally:
        remote.close()

    return ConnectResult(
        config_path=config_path,
        repo_id=resolved_repo_id,
        server_url=resolved_url,
        repository_registered=repository_registered,
        migrated_records=migrated_records,
        already_present_records=already_present_records,
        local_data_dir=local_data_dir,
        agent_configs=requested_agents,
        notes=tuple(notes),
    )
