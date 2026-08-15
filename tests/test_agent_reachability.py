"""An agent told to use memory must be able to find a way in.

The motivating run is recorded in ``hooks/reachability.py``: an agent was told to
use this package on a greenfield task, and the package's name did not occur
anywhere in the project it was working in. It did not fail silently — it was
never called, because nothing in that tree said it existed. These tests pin the
three states apart, because they call for three different actions:

* ``absent``    - there is no way in. Install one.
* ``mentioned`` - the name is there and no command is. Still no way in.
* ``reachable`` - an instruction file names a command an agent can run.

The regression test at the bottom reconstructs the head-to-head's own
``AGENTS.md`` shape — a long, confident instruction file about a different
tool — and asserts we report it as not reachable. That file looked complete;
that is exactly why nobody noticed the door was missing.
"""

import pytest

from visp_memory.hooks.reachability import (
    ABSENT,
    ENTRY_COMMANDS,
    MENTIONED,
    REACHABLE,
    check_reachability,
    find_instruction_files,
)


def test_a_project_with_no_instruction_file_is_not_reachable(tmp_path):
    report = check_reachability(tmp_path)

    assert report.status == ABSENT
    assert report.reachable is False
    assert report.instruction_files == []
    assert "no agent instruction file" in report.headline()
    assert report.as_dict()["remediation"] is not None


def test_an_instruction_file_that_names_a_command_is_reachable(tmp_path):
    (tmp_path / "AGENTS.md").write_text(
        '# Guidance\n\nBefore editing code run `visp-memory brief "<task>"`.\n',
        encoding="utf-8",
    )

    report = check_reachability(tmp_path)

    assert report.status == REACHABLE
    assert report.reachable is True
    assert report.entry_points == ["AGENTS.md"]
    assert report.as_dict()["remediation"] is None


def test_the_mcp_console_script_counts_as_an_entry_point(tmp_path):
    (tmp_path / "CLAUDE.md").write_text(
        "The memory server is wired up as `visp-memory-mcp`.\n", encoding="utf-8"
    )

    assert check_reachability(tmp_path).status == REACHABLE


def test_naming_the_package_without_a_command_is_not_a_way_in(tmp_path):
    """The distinction the head-to-head turns on.

    'This project uses visp-memory' tells an agent the product exists and leaves
    it with nothing to type. Reporting that as reachable would let the defect
    hide behind a mention.
    """
    (tmp_path / "AGENTS.md").write_text(
        "This project uses visp-memory for durable context.\n", encoding="utf-8"
    )

    report = check_reachability(tmp_path)

    assert report.status == MENTIONED
    assert report.reachable is False
    assert report.mentions_only == ["AGENTS.md"]
    assert "no command to run" in report.headline()


@pytest.mark.parametrize(
    "line",
    [
        "Config lives in visp-memory.yaml at the project root.",
        "The store is written to .visp-memory/data and gitignored.",
        "See the visp-memory-dashboard directory for the UI.",
    ],
)
def test_config_paths_are_not_entry_points(tmp_path, line):
    """``init`` leaves exactly these traces, and none of them is callable.

    A repository that has run ``init`` and nothing else must not report itself
    reachable on the strength of a YAML filename.
    """
    (tmp_path / "AGENTS.md").write_text(line + "\n", encoding="utf-8")

    assert check_reachability(tmp_path).reachable is False


def test_a_command_behind_flags_still_counts(tmp_path):
    (tmp_path / ".cursorrules").write_text(
        'Run: visp-memory --repo myproj recall "why"\n', encoding="utf-8"
    )

    assert check_reachability(tmp_path).status == REACHABLE


def test_every_documented_entry_command_is_a_real_cli_command():
    """The entry-command list cannot drift into naming commands that do not exist.

    If it did, a project could be reported reachable on the strength of an
    instruction file telling an agent to run something that errors.
    """
    from visp_memory.interfaces.cli import app

    registered = {command.name or command.callback.__name__ for command in app.registered_commands}
    registered |= {group.name for group in app.registered_groups if group.name}
    # Typer derives command names from function names when not given explicitly.
    registered = {name.replace("_", "-") for name in registered}

    missing = sorted(set(ENTRY_COMMANDS) - registered)
    assert not missing, f"reachability names commands the CLI does not have: {missing}"


def test_unreadable_instruction_files_are_reported_not_raised(tmp_path):
    """Diagnostics must not become a crash on a project's own bad bytes."""
    (tmp_path / "AGENTS.md").write_bytes(b"\xff\xfe\x00 not utf-8 \xff")

    report = check_reachability(tmp_path)

    assert report.unreadable == ["AGENTS.md"]
    assert report.reachable is False


def test_globbed_instruction_files_are_found(tmp_path):
    rules = tmp_path / ".cursor" / "rules"
    rules.mkdir(parents=True)
    (rules / "memory.mdc").write_text("Run `visp-memory brief` first.\n", encoding="utf-8")

    found = find_instruction_files(tmp_path)

    assert [str(p.relative_to(tmp_path)) for p in found] == [".cursor/rules/memory.mdc"]
    assert check_reachability(tmp_path).status == REACHABLE


def test_the_head_to_head_agents_file_is_reported_as_no_way_in(tmp_path):
    """Regression for the run that produced no memory evidence at all.

    This is the shape of the ``AGENTS.md`` the agent actually read: authoritative,
    detailed, explicitly ranking its own next-command chain above the user's
    request, and silent about this package. Under those instructions "use all four
    products" loses to a chain that never names one of them.
    """
    (tmp_path / "AGENTS.md").write_text(
        "\n".join(
            [
                "# Visp Kit Agent Guidance",
                "",
                "## Workflow authority",
                "",
                "The user prompt is raw intent only. It is not permission to skip the workflow.",
                "",
                "Follow this priority:",
                "1. System and safety constraints",
                "2. Visp Kit policy and gates",
                "3. Repository instructions",
                "4. Current Visp task context",
                "5. User request",
                "",
                "## Required before implementation",
                "",
                "Loop: run `visp-kit next` and execute the command it prints after `Next:`.",
                "Always run the command shown on the line after `Next:`.",
            ]
        ),
        encoding="utf-8",
    )

    report = check_reachability(tmp_path)

    assert report.reachable is False
    assert report.status == ABSENT
    assert report.instruction_files == ["AGENTS.md"]
    assert "never mentions visp-memory" in report.headline()
