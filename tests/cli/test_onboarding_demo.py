"""The walkthrough proves a round trip without polluting the current project."""

from typer.testing import CliRunner

from visp_memory.interfaces.cli import app


def test_demo_is_isolated_even_with_a_remote_storage_environment(cli_env, monkeypatch):
    config = cli_env / "visp-memory.yaml"
    config.write_text("project_name: keep-this\n", encoding="utf-8")
    monkeypatch.setenv("VISP_MEMORY_STORAGE_MODE", "client")
    monkeypatch.setenv("VISP_MEMORY_STORAGE_BACKEND", "neo4j")
    monkeypatch.setenv("VISP_MEMORY_EMBEDDING_PROVIDER", "openai")
    monkeypatch.setenv("VISP_MEMORY_STORAGE_DATA_DIR", str(cli_env / "real-data"))

    result = CliRunner().invoke(app, ["demo"])

    assert result.exit_code == 0, result.output
    assert "Capture and recall verified" in result.output
    assert "Screen wrap" in result.output
    assert "visp-memory init" in result.output
    assert "hooks install" in result.output
    assert config.read_text(encoding="utf-8") == "project_name: keep-this\n"
    assert sorted(p.name for p in cli_env.iterdir()) == ["visp-memory.yaml"]


def test_demo_does_not_claim_success_when_recall_misses(cli_env, monkeypatch):
    from visp_memory import Memory

    monkeypatch.setattr(Memory, "recall", lambda *args, **kwargs: [])
    result = CliRunner().invoke(app, ["demo"])
    assert result.exit_code != 0
    assert "Capture and recall verified" not in result.output
    assert "round trip failed" in result.output
