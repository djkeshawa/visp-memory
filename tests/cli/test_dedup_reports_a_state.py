"""`visp-memory dedup` must not print green for a check it never ran.

On the default configuration — sqlite backend, no ChromaDB collection because
embeddings are noop — `Memory.deduplicate()` took the "no collection" branch and
returned `[]`. The CLI read that as "nothing found" and printed
"No duplicates found." in green, exit code 0. Nothing had been compared.

This is the CLI half of `tests/quality/test_dedup_report.py`: the state the
Deduplicator now reports has to survive all the way to what the operator reads
and to the exit code a script branches on.
"""

from typer.testing import CliRunner

from visp_memory.interfaces.cli import app
from visp_memory.quality.dedup import DedupReport

runner = CliRunner()


def test_default_install_does_not_claim_a_clean_layer(cli_env):
    """The regression: sqlite + noop embeddings, so there is no collection to scan."""
    runner.invoke(app, ["init", "--type", "code"])

    result = runner.invoke(app, ["dedup"])

    assert "No duplicates found" not in result.output
    assert "Could not check for duplicates" in result.output
    # A script that branches on the exit code must not read this as clean.
    assert result.exit_code == 1


def test_a_completed_check_that_finds_nothing_says_so(cli_env, monkeypatch):
    runner.invoke(app, ["init", "--type", "code"])
    monkeypatch.setattr(
        "visp_memory.core.memory.Memory.deduplicate",
        lambda self, layer="episodic", threshold=0.9: DedupReport.clear(),
    )

    result = runner.invoke(app, ["dedup"])

    assert "no duplicates found" in result.output.lower()
    assert result.exit_code == 0


def test_a_completed_check_that_finds_duplicates_lists_them(cli_env, monkeypatch):
    runner.invoke(app, ["init", "--type", "code"])
    groups = [
        [
            {"id": "mem-a", "content": "Same fact", "similarity": 1.0},
            {"id": "mem-b", "content": "Same fact", "similarity": 0.97},
        ]
    ]
    monkeypatch.setattr(
        "visp_memory.core.memory.Memory.deduplicate",
        lambda self, layer="episodic", threshold=0.9: DedupReport.checked(groups),
    )

    result = runner.invoke(app, ["dedup"])

    assert "Found 1 potential duplicates" in result.output
    assert "mem-a" in result.output
    assert "mem-b" in result.output
    assert result.exit_code == 0
