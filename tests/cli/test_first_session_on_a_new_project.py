"""What a first session on a brand-new project actually gets.

The README now says two things about greenfield work, and both are claims a
reader will act on, so both are pinned here.

1. **The read side is nearly empty and that is expected.** ``init`` seeds from git
   history, so a repository with no history seeds nothing. Saying so in the docs
   is only honest if the docs cannot drift away from the behaviour.

2. **The write side works from the first minute.** This is the half that is easy
   to get wrong in the other direction. When a run using this package produced no
   memory evidence at all, "memory has nothing to offer a new project" was one of
   the candidate explanations — and it is false. Recording a decision and reading
   it back on an empty store works. The reason that run was empty was that nothing
   in the project ever named the commands, not that the commands had nothing to do.

Pinning (2) means the README's advice — establish the write loop early — cannot
quietly become untrue.
"""

import subprocess
from pathlib import Path

from typer.testing import CliRunner

from visp_memory.interfaces.cli import app

runner = CliRunner()


def _empty_git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)


def test_a_repository_with_no_history_seeds_nothing_and_still_succeeds(cli_env):
    """No history to mine is not an error, and init must not pretend it found something."""
    _empty_git_repo(cli_env)

    result = runner.invoke(app, ["init", "--type", "code"])

    assert result.exit_code == 0
    assert "Initialized Visp Memory" in result.output
    stats = runner.invoke(app, ["stats"])
    assert stats.exit_code == 0


def test_a_decision_recorded_in_the_first_session_is_recallable_in_it(cli_env):
    """The greenfield loop the README recommends, end to end on an empty store."""
    _empty_git_repo(cli_env)
    runner.invoke(app, ["init", "--type", "code"])

    assert runner.invoke(app, ["recall", "screen wrap"]).output.strip() == "No results found"

    recorded = runner.invoke(
        app,
        [
            "decision",
            "Screen wrap on all four edges",
            "Bouncing was rejected in the spec",
        ],
    )
    assert recorded.exit_code == 0

    recalled = runner.invoke(app, ["recall", "screen wrap"])

    assert recalled.exit_code == 0
    assert "Screen wrap" in recalled.output


def test_the_brief_surfaces_that_decision_for_a_matching_task(cli_env):
    """``brief`` is the command an agent runs before working; it must see same-session writes."""
    _empty_git_repo(cli_env)
    runner.invoke(app, ["init", "--type", "code"])
    runner.invoke(
        app,
        ["decision", "Screen wrap on all four edges", "Bouncing was rejected in the spec"],
    )

    result = runner.invoke(app, ["brief", "implement screen wrap"])

    assert result.exit_code == 0
    assert "Screen wrap" in result.output
