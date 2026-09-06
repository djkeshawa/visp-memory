import json

from typer.testing import CliRunner

from visp_memory.core.storage import LocalStorage
from visp_memory.interfaces.cli import app, get_memory


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
