"""A store owned by a server is refused in one clear line, not a traceback."""

import json

import pytest
from typer.testing import CliRunner

from tests.core.test_writer_lock_processes import spawned_role
from visp_memory.core.writer_lock import WriterLockConflict
from visp_memory.interfaces.cli import app

runner = CliRunner()


@pytest.fixture
def served_store(cli_env, monkeypatch):
    """A live server process on the store the CLI would open."""
    data_dir = cli_env / ".visp-memory" / "data"
    monkeypatch.setenv("VISP_MEMORY_STORAGE_DATA_DIR", str(data_dir))
    with spawned_role(data_dir, "server") as (server, conflict):
        assert conflict is None
        yield server


def assert_one_clear_refusal(result, server):
    assert result.exit_code == 1
    assert not isinstance(result.exception, WriterLockConflict), result.output
    assert "Traceback" not in result.output
    assert f"a server (pid: {server.pid})" in result.output
    assert "storage.mode: client" in result.output
    assert len(result.output.strip().splitlines()) <= 3, result.output


@pytest.mark.parametrize("command", [["recall", "build tool"], ["learn", "Use pnpm."]])
def test_memory_commands_explain_a_serving_owner(served_store, command):
    assert_one_clear_refusal(runner.invoke(app, command), served_store)


def test_init_explains_a_serving_owner(served_store):
    result = runner.invoke(app, ["init", "--no-mine"])
    assert_one_clear_refusal(result, served_store)


@pytest.mark.parametrize(
    "command", [["contract", "recall", "build tool"], ["contract", "propose", "Use pnpm."]]
)
def test_contract_commands_keep_their_json_envelope(served_store, command):
    result = runner.invoke(app, command)
    assert result.exit_code == 1
    envelope = json.loads(result.stdout.strip().splitlines()[-1])
    assert envelope["success"] is False
    assert f"a server (pid: {served_store.pid})" in envelope["reason"]
