"""Portable SQLite backups and explicit upgrades without opening an old store."""

import hashlib
import json
import os
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

from visp_memory.core.clock import utc_now_iso
from visp_memory.core.storage import STORAGE_SCHEMA_VERSION, LocalStorage


def backup_storage(data_dir: Path, destination: Path) -> dict:
    """Copy a stopped SQLite store, using SQLite backups to include WAL contents."""
    source = Path(data_dir).resolve()
    target = Path(destination).resolve()
    if not (source / "memories.db").is_file():
        raise ValueError("No memories.db found in the selected storage directory")
    if source == target or source in target.parents or target in source.parents:
        raise ValueError("Backup destination must be outside the storage directory")
    if target.exists():
        raise FileExistsError("Backup destination already exists; choose a new directory")
    if any(p.is_symlink() for p in source.rglob("*")):
        raise ValueError("Storage contains symbolic links; back up their targets separately")
    target.mkdir(parents=True, mode=0o700)
    files = []
    try:
        for path in source.rglob("*"):
            relative = path.relative_to(source)
            if "backups" in relative.parts or path.is_dir():
                continue
            if path.name.endswith(("-wal", "-shm", "-journal")):
                continue
            output = target / relative
            output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with path.open("rb") as stream:
                sqlite_file = stream.read(16) == b"SQLite format 3\x00"
            if sqlite_file:
                with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
                    with closing(sqlite3.connect(output)) as copied:
                        db.backup(copied)
                        copied.execute("PRAGMA journal_mode=DELETE")
                        if copied.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                            raise ValueError(f"Backup integrity check failed: {relative}")
            else:
                shutil.copyfile(path, output)
            os.chmod(output, 0o600)
            files.append({"path": relative.as_posix(), "sha256": _digest(output)})
        manifest = {
            "format": "visp-memory-backup-v1",
            "created_at": utc_now_iso(),
            "source": str(source),
            "files": files,
        }
        (target / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        os.chmod(target / "manifest.json", 0o600)
        return {"backup": str(target), "files": len(files)}
    except Exception:
        # This directory was created exclusively by this operation.
        shutil.rmtree(target)
        raise


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def upgrade_storage(data_dir: Path, destination: Path) -> dict:
    """Back up every local database before the supported schema migration."""
    source = Path(data_dir).resolve()
    if not (source / "memories.db").is_file():
        raise ValueError("No memories.db found in the selected storage directory")
    with closing(sqlite3.connect((source / "memories.db").as_uri() + "?mode=ro", uri=True)) as db:
        version = db.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
    if version == STORAGE_SCHEMA_VERSION:
        return {"status": "already_current", "version": version}
    if version not in {2, 3, 4}:
        raise ValueError(f"Unsupported storage schema {version}; no changes made")
    backup = backup_storage(source, destination)
    result = LocalStorage.migrate_schema(
        source, backup_path=Path(destination) / "migration-source.db"
    )
    return {**result, **backup}
