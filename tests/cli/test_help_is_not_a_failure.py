"""Asking the CLI what it can do succeeds (LC-91).

`no_args_is_help` prints the help and then exits non-zero, so `visp-memory` — the
first thing anyone types — reported failure while showing the page it was supposed
to show. Anything reading the exit code (a shell `set -e`, a wrapper script, an
installer smoke test) is told the CLI is broken.
"""

import pytest
from typer.testing import CliRunner

from visp_memory.interfaces.cli import app

runner = CliRunner()

GROUPS_WITH_SUBCOMMANDS = ["intent", "review", "quality", "capture", "repos", "contract"]


def test_the_bare_command_shows_the_help_and_succeeds():
    result = runner.invoke(app, [])

    assert result.exit_code == 0
    assert "Usage" in result.output


def test_the_help_flag_succeeds():
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "Commands" in result.output


@pytest.mark.parametrize("group", GROUPS_WITH_SUBCOMMANDS)
def test_a_bare_subcommand_group_shows_its_help_and_succeeds(group):
    result = runner.invoke(app, [group])

    assert result.exit_code == 0, result.output
    assert "Usage" in result.output


def test_an_unknown_command_is_still_a_usage_error():
    result = runner.invoke(app, ["definitely-not-a-command"])

    assert result.exit_code != 0
