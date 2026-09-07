"""Hook previews leave instructions, settings, backups and storage untouched."""

from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

import visp_memory.interfaces.cli as cli
from visp_memory import MemoryConfig

TOOLS = ["generic", "cursor", "claude-code", "aider", "codex"]


def snapshot(root):
    return {
        str(path.relative_to(root)): path.read_bytes() if path.is_file() else None
        for path in root.rglob("*")
    }


@pytest.mark.parametrize("tool", TOOLS)
@pytest.mark.parametrize("operation", ["install", "uninstall"])
@pytest.mark.parametrize("existing", [False, True])
def test_dry_run_preserves_all_project_files(cli_env, monkeypatch, tool, operation, existing):
    config_path = cli_env / "codex.toml"
    config = MemoryConfig(repo_id="repo-a")
    monkeypatch.setattr(cli, "get_memory", lambda: SimpleNamespace(config=config))
    args = [tool, "--config-path", str(config_path)]
    if existing:
        installed = CliRunner().invoke(cli.app, ["hooks", "install", *args])
        assert installed.exit_code == 0, installed.output
    before = snapshot(cli_env)

    result = CliRunner().invoke(cli.app, ["hooks", operation, *args, "--dry-run"])

    assert result.exit_code == 0, result.output
    assert snapshot(cli_env) == before


@pytest.mark.parametrize("operation", ["install", "uninstall"])
def test_preview_does_not_initialize_storage(cli_env, monkeypatch, operation):
    def refuse_storage():
        raise AssertionError("A hook preview must not initialize a memory store")

    monkeypatch.setattr(cli, "get_memory", refuse_storage)
    before = snapshot(cli_env)
    result = CliRunner().invoke(cli.app, ["hooks", operation, "generic", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert snapshot(cli_env) == before


@pytest.mark.parametrize("dry_run", [False, True])
def test_conflicting_codex_config_returns_failure(cli_env, monkeypatch, dry_run):
    config_path = cli_env / "codex.toml"
    original = '[mcp_servers.visp-memory]\ncommand = "another-managed-server"\n'
    config_path.write_text(original)
    monkeypatch.setattr(
        cli, "get_memory", lambda: SimpleNamespace(config=MemoryConfig(repo_id="repo-a"))
    )
    args = ["hooks", "install", "codex", "--config-path", str(config_path)]
    result = CliRunner().invoke(cli.app, args + (["--dry-run"] if dry_run else []))

    assert result.exit_code == 1, result.output
    assert "codex_mcp_config" in result.output
    assert "integration installed!" not in result.output
    assert config_path.read_text() == original


@pytest.mark.parametrize("dry_run", [False, True])
def test_failed_auto_hook_install_returns_failure(cli_env, monkeypatch, dry_run):
    monkeypatch.setattr(cli, "get_memory", lambda: SimpleNamespace())
    settings = cli_env / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_text("[]")

    result = CliRunner().invoke(
        cli.app, ["hooks", "install", "claude-code"] + (["--dry-run"] if dry_run else [])
    )

    assert result.exit_code == 1, result.output
    assert "integration installed!" not in result.output
    assert settings.read_text() == "[]"


@pytest.mark.parametrize("tool", ["cursor", "claude-code"])
@pytest.mark.parametrize("dry_run", [False, True])
def test_repeated_uninstall_is_a_successful_noop(cli_env, monkeypatch, tool, dry_run):
    monkeypatch.setattr(cli, "get_memory", lambda: SimpleNamespace())
    runner = CliRunner()
    installed = runner.invoke(cli.app, ["hooks", "install", tool])
    assert installed.exit_code == 0, installed.output
    removed = runner.invoke(cli.app, ["hooks", "uninstall", tool])
    assert removed.exit_code == 0, removed.output
    before = snapshot(cli_env)

    result = runner.invoke(
        cli.app, ["hooks", "uninstall", tool] + (["--dry-run"] if dry_run else [])
    )

    assert result.exit_code == 0, result.output
    assert snapshot(cli_env) == before


@pytest.mark.parametrize(
    "tool,filename,marker",
    [
        ("cursor", ".cursorrules", "# LLM-MEMORY"),
        ("claude-code", "CLAUDE.md", "<!-- LLM-MEMORY -->"),
    ],
)
@pytest.mark.parametrize("dry_run", [False, True])
def test_uninstall_refuses_partial_markers(
    cli_env, monkeypatch, tool, filename, marker, dry_run
):
    monkeypatch.setattr(cli, "get_memory", lambda: SimpleNamespace())
    (cli_env / filename).write_text(f"Handwritten guidance.\n{marker} START\n")
    before = snapshot(cli_env)

    result = CliRunner().invoke(
        cli.app, ["hooks", "uninstall", tool] + (["--dry-run"] if dry_run else [])
    )

    assert result.exit_code == 1, result.output
    assert snapshot(cli_env) == before


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize(
    "markers",
    [
        ["START"],
        ["END"],
        ["END", "START"],
        ["START", "START", "END"],
    ],
)
def test_codex_install_refuses_malformed_instruction_markers(
    cli_env, monkeypatch, dry_run, markers
):
    monkeypatch.setattr(
        cli, "get_memory", lambda: SimpleNamespace(config=MemoryConfig(repo_id="repo-a"))
    )
    agents = cli_env / "AGENTS.md"
    original = "Handwritten project guidance.\n" + "\n".join(
        f"<!-- LLM-MEMORY-CODEX --> {marker}" for marker in markers
    )
    agents.write_text(original)
    args = ["hooks", "install", "codex", "--config-path", str(cli_env / "codex.toml")]

    result = CliRunner().invoke(cli.app, args + (["--dry-run"] if dry_run else []))

    assert result.exit_code == 1, result.output
    assert "agents_instructions" in result.output
    assert "integration installed!" not in result.output
    assert "integration validated!" not in result.output
    assert agents.read_text() == original
    assert not agents.with_suffix(".md.backup").exists()
