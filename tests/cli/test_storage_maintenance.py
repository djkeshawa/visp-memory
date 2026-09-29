import json

import pytest
from typer.testing import CliRunner

from tests.core.test_writer_lock_processes import spawned_role
from visp_memory.core.storage import LocalStorage
from visp_memory.interfaces.cli import app, get_memory


@pytest.mark.parametrize("command", ["backup", "upgrade"])
@pytest.mark.parametrize("offline", [False, True])
def test_client_maintenance_explains_stopped_server_offline_data_directory(
    cli_env, monkeypatch, command, offline,
):
    monkeypatch.setenv("VISP_MEMORY_STORAGE_MODE", "client")
    monkeypatch.setenv("VISP_MEMORY_STORAGE_DATA_DIR", str(cli_env / "client-data"))
    destination = cli_env / "backup"
    args = [str(destination)] if command == "backup" else ["--backup-dir", str(destination)]
    if offline:
        args.append("--offline")
    result = CliRunner().invoke(app, ["storage", command, *args])
    assert result.exit_code != 0
    assert "stop the server" in result.output.lower()
    assert "--offline" in result.output
    assert "--data-dir" in result.output
    assert "client mode" in result.output.lower()
    assert not destination.exists()


@pytest.mark.parametrize("command", ["backup", "upgrade"])
def test_client_maintenance_can_run_offline_against_explicit_data_dir(
    cli_env, monkeypatch, command,
):
    monkeypatch.setenv("VISP_MEMORY_STORAGE_MODE", "client")
    source = cli_env / "server-data"
    storage = LocalStorage(source)
    storage.store_memory("Keep server data")
    storage.close()
    destination = cli_env / "backup"
    args = [str(destination)] if command == "backup" else ["--backup-dir", str(destination)]
    result = CliRunner().invoke(
        app, ["storage", command, *args, "--offline", "--data-dir", str(source)],
    )
    assert result.exit_code == 0, result.output
    if command == "backup":
        assert (destination / "memories.db").is_file()
    else:
        assert json.loads(result.output)["status"] == "already_current"


def test_backup_requires_offline_and_preserves_data(cli_env):
    source = cli_env / "data"
    store = LocalStorage(source)
    memory_id = store.store_memory("Keep this", auto_link=False)
    destination = cli_env / "backup"
    args = ["storage", "backup", str(destination), "--data-dir", str(source)]
    assert CliRunner().invoke(app, args).exit_code != 0
    assert not destination.exists()
    result = CliRunner().invoke(app, [*args, "--offline"])
    assert result.exit_code == 0, result.output
    assert LocalStorage(destination).get_memory(memory_id)["content"] == "Keep this"


def test_cli_reports_explicit_completion(cli_env):
    memory = get_memory()
    intent_id = memory._storage.set_intent("Deliver login", repo_id="demo")
    payload = {
        "source": "assistant",
        "task_id": "task-1",
        "event_id": "event-1",
        "revision": 1,
        "status": "completed",
        "summary": "Login delivered",
        "evidence": [{"description": "Acceptance tests passed"}],
    }
    report = cli_env / "report.json"
    report.write_text(json.dumps(payload))
    result = CliRunner().invoke(app, ["intent", "report", intent_id, "--file", str(report)])
    assert result.exit_code == 0, result.output
    assert memory._storage.get_active_intents(repo_id="demo") == []
    assert memory._storage.get_active_intents(status="completed")[0]["id"] == intent_id


def test_offline_backup_refused_while_server_is_running(cli_env):
    source = cli_env / "data"
    store = LocalStorage(source)
    store.close()
    destination = cli_env / "backup"
    with spawned_role(source, "server") as (child, conflict):
        assert conflict is None
        holder_pid = child.pid
        result = CliRunner().invoke(app, [
            "storage", "backup", str(destination), "--data-dir", str(source), "--offline",
        ])
    assert result.exit_code != 0
    assert "stop the server" in result.output.lower()
    assert str(holder_pid) in result.output
    assert not destination.exists()
