"""Explicit, offline storage maintenance commands."""

import json
import sqlite3
from pathlib import Path

import typer

from visp_memory.config import load_config
from visp_memory.core.maintenance import backup_storage, upgrade_storage
from visp_memory.core.writer_lock import WriterLockConflict, acquire_writer_lock

app = typer.Typer(help="Back up and upgrade a stopped SQLite store.", no_args_is_help=True)


def _run(operation, destination: Path, data_dir: Path | None, stopped: bool):
    config = load_config()
    if config.storage.mode == "client" and (not stopped or data_dir is None):
        typer.echo(
            "Storage maintenance is offline-only in client mode. Stop the server, "
            "then run this command with --offline --data-dir <server-data-dir>.",
            err=True,
        )
        raise typer.Exit(1)
    if not stopped:
        raise typer.BadParameter("Stop the server and other writers, then pass --offline")
    if config.storage.backend != "sqlite":
        raise typer.BadParameter("These commands support SQLite storage only")
    try:
        source = data_dir or config.storage.data_dir
        with acquire_writer_lock(source, "server"):
            result = operation(source, destination)
    except WriterLockConflict as error:
        typer.echo(f"Stop the server and other writers first. {error}", err=True)
        raise typer.Exit(1) from error
    except (ValueError, OSError, sqlite3.Error) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
    typer.echo(json.dumps(result))


@app.command("backup")
def backup(
    destination: Path = typer.Argument(..., help="New backup directory outside the store"),
    data_dir: Path | None = typer.Option(None, "--data-dir"),
    offline: bool = typer.Option(False, help="Confirm that server and other writers are stopped"),
):
    """Back up memories, credentials, lifecycle data, and local indexes."""
    _run(backup_storage, destination, data_dir, offline)


@app.command("upgrade")
def upgrade(
    backup_dir: Path = typer.Option(..., "--backup-dir", help="New directory for a full backup"),
    data_dir: Path | None = typer.Option(None, "--data-dir"),
    offline: bool = typer.Option(False, help="Confirm that server and other writers are stopped"),
):
    """Back up the stopped store and migrate it to the current schema."""
    _run(upgrade_storage, backup_dir, data_dir, offline)
