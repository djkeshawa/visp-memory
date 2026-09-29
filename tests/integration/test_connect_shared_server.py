import yaml
from typer.testing import CliRunner

from visp_memory import Memory, MemoryConfig
from visp_memory.interfaces.cli import app
from visp_memory.server.app import app as server_app

pytest_plugins = ["tests.integration.shared_server"]


def test_connect_migrates_local_records_through_the_shared_server(
    shared_server, tmp_path, monkeypatch
):
    project = tmp_path / "project"
    project.mkdir()
    local_data = project / ".visp-memory" / "data"
    config = MemoryConfig(repo_id="migration-repo", project_name="migration-repo")
    config.storage.data_dir = local_data
    config.storage.mode = "local"
    config.embedding.provider = "noop"
    config.save(project / "visp-memory.yaml")
    local = Memory(config=config)
    memory_id = local.record("Migrate this local observation")
    local.close()
    original_database = local_data / "memories.db"
    assert original_database.is_file()
    monkeypatch.chdir(project)

    result = CliRunner().invoke(
        app,
        ["connect", "--server-url", shared_server, "--migrate-local"],
    )

    assert result.exit_code == 0, result.output
    connected = yaml.safe_load((project / "visp-memory.yaml").read_text(encoding="utf-8"))
    assert connected["repo_id"] == "migration-repo"
    assert connected["storage"]["mode"] == "client"
    assert connected["storage"]["server_url"] == shared_server
    assert server_app.state.storage.peek_memory(memory_id)["content"] == (
        "Migrate this local observation"
    )
    assert server_app.state.storage.get_repository("migration-repo")["name"] == (
        "migration-repo"
    )
    assert original_database.is_file()
    assert str(local_data.resolve()) in result.output
