"""A frozen feature must look frozen where the user actually looks.

docs/FEATURE_STATUS.md freezes teams/users/JWT auth and cross-repo aggregation:
implemented and tested, not actively developed, kept working rather than
extended. That was true in the document and invisible everywhere else. A user
who installs the package and runs `visp-memory --help` sees `teams`, `admin` and
`repos` sitting alongside `recall` and `record` with no distinction, and the
help text is the only status most people will ever read.

These tests pin the two statements to each other. If a group is unfrozen, the
document and the help string have to move together.
"""

from pathlib import Path

from typer.testing import CliRunner

from visp_memory.interfaces.cli import app

runner = CliRunner()

FEATURE_STATUS = Path(__file__).resolve().parents[2] / "docs" / "FEATURE_STATUS.md"

# Group name -> the FEATURE_STATUS row that freezes it.
FROZEN_GROUPS = {
    "teams": "Teams, users, JWT auth",
    "admin": "Teams, users, JWT auth",
    "repos": "Cross-repo aggregation",
}


def test_feature_status_still_freezes_what_the_cli_says_is_frozen():
    """The document is the source of truth; the help string quotes it."""
    text = FEATURE_STATUS.read_text(encoding="utf-8")

    for row in set(FROZEN_GROUPS.values()):
        line = next((ln for ln in text.splitlines() if ln.startswith(f"| {row} ")), None)
        assert line is not None, f"FEATURE_STATUS.md no longer has a row for {row!r}"
        assert "Frozen" in line, (
            f"{row!r} is no longer Frozen in FEATURE_STATUS.md, but the CLI still "
            "labels its command group [FROZEN]. Update both."
        )


def _help_row(group: str) -> str:
    """The single rendered `--help` line that introduces `group`.

    Rich wraps to the terminal width, so the width is pinned: a narrow terminal
    must not be able to decide whether this test passes.
    """
    result = runner.invoke(app, ["--help"], env={"COLUMNS": "120", "TERM": "dumb"})
    assert result.exit_code == 0

    rows = [line for line in result.output.splitlines() if f" {group} " in line]
    assert rows, f"no --help row rendered for {group!r}; did the group vanish?"
    return rows[0]


def test_frozen_groups_are_labelled_in_the_top_level_help():
    for group in FROZEN_GROUPS:
        row = _help_row(group)
        assert "[FROZEN]" in row, (
            f"the `{group}` group is Frozen in FEATURE_STATUS.md but its --help row "
            f"does not say so: {row.strip()!r}. A frozen feature that looks "
            "first-class is a claim."
        )


def test_supported_groups_are_not_labelled_frozen():
    """The label has to mean something, so it must not be everywhere."""
    for group in ("contract", "review", "hooks", "capture"):
        assert "[FROZEN]" not in _help_row(group)
