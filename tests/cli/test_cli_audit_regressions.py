"""CLI commands report what is true, and touch only what they say they touch."""

import json
import os
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

import visp_memory.interfaces.cli as cli
from visp_memory import Memory
from visp_memory.config import load_config
from visp_memory.core.storage import LocalStorage
from visp_memory.interfaces.cli import app, ensure_gitignored

runner = CliRunner()


def _git_repo(path: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    return path


def _ignored(root: Path, relative: str) -> bool:
    return subprocess.run(
        ["git", "check-ignore", "-q", relative], cwd=root, check=False
    ).returncode == 0


def test_custom_data_dir_ignores_only_the_store(tmp_path):
    root = _git_repo(tmp_path)

    added = ensure_gitignored(root, Path("src") / "memstore" / "data")

    assert added == ["src/memstore/data/"]
    assert _ignored(root, "src/memstore/data/memories.db")
    assert not _ignored(root, "src/app.py")


@pytest.mark.parametrize("data_dir", ["/var/lib/visp/data", "../shared/data"])
def test_store_outside_the_project_is_not_gitignored(tmp_path, data_dir):
    root = _git_repo(tmp_path)

    assert ensure_gitignored(root, Path(data_dir)) == []
    assert not (root / ".gitignore").exists()


def test_init_with_custom_data_keeps_source_tree_tracked(cli_env):
    _git_repo(cli_env)

    result = runner.invoke(app, ["init", "--data", "src/memstore", "--no-mine"])

    assert result.exit_code == 0, result.output
    lines = (cli_env / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "src/" not in lines
    assert "src/memstore/data/" in lines


def test_audit_counts_every_page(cli_env, monkeypatch):
    runner.invoke(app, ["init", "--no-mine"])
    with Memory(config=load_config()) as memory:
        for number in range(5):
            memory.record(f"event {number} token{number}", category="note")
    # Model the server, which caps one /memories page; audit must keep paging.
    capped = LocalStorage.list_memories

    def list_one_page(self, *args, limit=50, **kwargs):
        return capped(self, *args, limit=min(limit, 2), **kwargs)

    monkeypatch.setattr(LocalStorage, "list_memories", list_one_page)
    monkeypatch.setattr(cli, "_AUDIT_PAGE_SIZE", 2)
    cli._memory = None

    result = runner.invoke(app, ["audit", "--json", "--limit", "1"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["total"] == 5


def test_convention_short_r_is_repo_like_every_other_command(monkeypatch):
    calls = []

    class Semantic:
        def convention(self, rule, rationale, repo_id, _write_channel):
            calls.append((rule, rationale, repo_id))

    class FakeMemory:
        config = type("Config", (), {"repo_id": "default-repo"})()
        semantic = Semantic()

    monkeypatch.setattr(cli, "get_memory", lambda: FakeMemory())

    for flag in ("-r", "-rp", "--repo"):
        result = runner.invoke(
            app, ["convention", "Use tabs", flag, "other-repo", "--rationale", "consistency"],
        )
        assert result.exit_code == 0, result.output

    assert calls == [("Use tabs", "consistency", "other-repo")] * 3


def test_doctor_does_not_create_the_store_it_inspects(cli_env):
    (cli_env / "visp-memory.yaml").write_text(
        "repo_id: demo\nstorage:\n  data_dir: not-yet/data\n", encoding="utf-8",
    )

    result = runner.invoke(app, ["doctor", "--format", "json"])

    assert not (cli_env / "not-yet").exists()
    payload = json.loads(result.output)
    assert payload["storage"]["status"] == "not_created"


@pytest.mark.skipif(
    not hasattr(os, "geteuid") or os.geteuid() == 0, reason="root can write anywhere"
)
def test_doctor_reports_an_unwritable_data_dir(cli_env):
    data_dir = cli_env / "locked"
    data_dir.mkdir()
    data_dir.chmod(0o500)
    (cli_env / "visp-memory.yaml").write_text(
        f"repo_id: demo\nstorage:\n  data_dir: {data_dir}\n", encoding="utf-8",
    )
    try:
        payload = cli._storage_doctor_status(load_config())
    finally:
        data_dir.chmod(0o700)

    assert payload["status"] == "unwritable_data_dir"
    assert payload["connected"] is False


@pytest.mark.parametrize("output_format", ["text", "json"])
def test_repos_context_for_an_unknown_repository_fails(cli_env, output_format):
    runner.invoke(app, ["init", "--no-mine"])
    cli._memory = None

    result = runner.invoke(
        app, ["repos", "context", "no-such-repo", "--format", output_format],
    )

    assert result.exit_code == 1
    if output_format == "json":
        assert json.loads(result.output) == {"error": "Repository not found"}
    else:
        assert "Repository not found" in result.output
