"""Repository context preserves the requested runtime and temporal scope."""

from types import SimpleNamespace

from typer.testing import CliRunner

import visp_memory.interfaces.cli as cli
from visp_memory.core.cross_repo import CrossRepoContext


def test_repo_context_forwards_runtime_and_time_scope(cli_env, monkeypatch):
    seen = {}

    def context(self, repo_id, **kwargs):
        seen.update(repo_id=repo_id, **kwargs)
        return {"warnings": [], "breaking_changes": []}

    monkeypatch.setattr(
        cli, "get_memory", lambda: SimpleNamespace(repos=SimpleNamespace(storage=None))
    )
    monkeypatch.setattr(CrossRepoContext, "get_context_for_repo", context)

    result = CliRunner().invoke(cli.app, [
        "repos", "context", "repo-a", "--format", "json",
        "--environment", "production", "--task-type", "migration",
        "--as-of", "2026-01-01T00:00:00Z",
    ])

    assert result.exit_code == 0, result.output
    assert seen == {
        "repo_id": "repo-a", "environment": "production", "task_type": "migration",
        "as_of": "2026-01-01T00:00:00Z",
    }
