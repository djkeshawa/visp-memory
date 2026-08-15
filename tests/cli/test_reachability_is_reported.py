"""An absent entry point has to be a visible state, not a silence.

``visp-memory init`` used to end on ``Try: visp-memory recall "why"`` and leave
behind a config file and a gitignored data directory. Nothing it wrote was
addressed to an agent, and nothing it printed said so. A repository in that state
is indistinguishable, from inside a coding agent, from one where memory was never
installed — which is how a run that was told to use memory produced no memory
evidence at all.

These tests pin the reporting, not the wording: ``doctor --format json`` carries a
machine-readable reachability block, and ``init`` says out loud when it has just
finished setting up something no agent can find, naming the command that fixes it.
"""

import json

from typer.testing import CliRunner

from visp_memory.hooks.reachability import REMEDIATION
from visp_memory.interfaces.cli import app

runner = CliRunner()


def test_doctor_json_reports_reachability(cli_env):
    runner.invoke(app, ["init", "--type", "code", "--no-mine"])

    result = runner.invoke(app, ["doctor", "--format", "json"])

    assert result.exit_code == 0
    block = json.loads(result.output)["agent_reachability"]
    assert block["reachable"] is False
    assert block["status"] == "absent"
    assert block["remediation"] == REMEDIATION


def test_doctor_json_reports_reachable_once_an_entry_point_exists(cli_env):
    runner.invoke(app, ["init", "--type", "code", "--no-mine"])
    (cli_env / "AGENTS.md").write_text(
        'Before editing, run `visp-memory brief "<task>"`.\n', encoding="utf-8"
    )

    result = runner.invoke(app, ["doctor", "--format", "json"])

    block = json.loads(result.output)["agent_reachability"]
    assert block["reachable"] is True
    assert block["entry_points"] == ["AGENTS.md"]
    assert block["remediation"] is None


def test_doctor_text_names_the_state_and_the_fix(cli_env):
    runner.invoke(app, ["init", "--type", "code", "--no-mine"])

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert "Agent reachability" in result.output
    assert "not reachable" in result.output
    assert "hooks install" in result.output


def test_init_says_no_agent_can_find_this_yet(cli_env):
    """The moment the gap is created is the moment to say so."""
    result = runner.invoke(app, ["init", "--type", "code", "--no-mine"])

    assert result.exit_code == 0
    assert "not reachable" in result.output
    assert "hooks install" in result.output


def test_init_does_not_nag_when_an_entry_point_is_already_there(cli_env):
    (cli_env / "AGENTS.md").write_text(
        'Run `visp-memory recall "<task>"` before editing.\n', encoding="utf-8"
    )

    result = runner.invoke(app, ["init", "--type", "code", "--no-mine"])

    assert result.exit_code == 0
    assert "not reachable" not in result.output
    assert "hooks install" not in result.output


def test_init_reports_reachability_even_when_history_mining_is_skipped(cli_env):
    """``--no-mine`` returns early; the reachability report must not be inside that path.

    A project that seeds nothing is the greenfield case, and the greenfield case
    is precisely the one that needs to be told it has no entry point.
    """
    result = runner.invoke(app, ["init", "--type", "code", "--no-mine"])

    assert "Agent reachability" in result.output
