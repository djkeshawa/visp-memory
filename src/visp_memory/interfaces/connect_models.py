"""Small value types shared by the connect workflow."""

from dataclasses import dataclass, field
from pathlib import Path


class ConnectError(ValueError):
    """A connect request that cannot be completed safely."""


@dataclass(frozen=True)
class ServerRecord:
    """Non-secret discovery metadata written beside the shared owner token."""

    path: Path
    pid: int | None
    url: str
    data_dir: Path | None


@dataclass(frozen=True)
class ConnectResult:
    config_path: Path
    repo_id: str
    server_url: str
    repository_registered: bool
    migrated_records: int = 0
    already_present_records: int = 0
    local_data_dir: Path | None = None
    agent_configs: tuple[str, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)
