"""Recall answers goal-shaped queries, not just keyword-shaped ones.

THE DEFECT THIS PINS. The coordinator's memory fusion asks with the task goal
("add a farewell message to app.js"). Lexical overlap divided matched terms by
ALL query terms, so every filler word diluted the score below the recall
threshold — the same store answered "app.js" with a hit and the sentence with
nothing. Memory that only answers queries nobody actually sends is decorative.

The repair caps the effective query length for recall scoring only.
relationship_score keeps the strict ratio: graph links are deliberately
conservative, and loosening them was not the goal.
"""

import json

import pytest
from typer.testing import CliRunner

from visp_memory.interfaces.cli import app

runner = CliRunner()


def _invoke(*args: str):
    return runner.invoke(app, list(args))


@pytest.mark.usefixtures("cli_env")
class TestGoalShapedRecall:
    def test_a_goal_sentence_finds_the_matching_memory(self):
        recorded = _invoke(
            "record",
            "Due dates are stored as plain YYYY-MM-DD strings in todos.json",
            "--repo",
            "demo-repo",
        )
        assert recorded.exit_code == 0, recorded.output

        result = _invoke(
            "contract",
            "recall",
            "add an overdue marker that compares due dates in todos.json",
            "--repo",
            "demo-repo",
        )

        assert result.exit_code == 0, result.output
        envelope = json.loads(result.output.strip().splitlines()[-1])
        contents = " ".join(entry["content"] for entry in envelope["entries"])
        assert "YYYY-MM-DD" in contents, envelope

    def test_an_unrelated_goal_still_returns_nothing(self):
        recorded = _invoke(
            "record", "The deploy pipeline uses blue-green cutover", "--repo", "demo-repo"
        )
        assert recorded.exit_code == 0, recorded.output

        result = _invoke(
            "contract",
            "recall",
            "tune the garbage collector pause budget for the cache shard",
            "--repo",
            "demo-repo",
        )

        envelope = json.loads(result.output.strip().splitlines()[-1])
        assert envelope["entries"] == [], envelope


@pytest.mark.usefixtures("cli_env")
class TestContractMinScoreOverride:
    """The coordinator may deliberately trade precision for recall.

    Its memory pack is an explicitly untrusted, budget-capped digest, so a
    weak-but-real association ("overdue" shared between a decision and a new
    goal) is worth surfacing there — while the DEFAULT floor, which the human
    recall CLI and every benchmark pin, stays exactly where it was.
    """

    def test_lower_floor_surfaces_a_weak_association(self):
        recorded = _invoke(
            "record",
            "Decision: overdue todos are those due strictly before today's local date",
            "--repo",
            "demo-repo",
        )
        assert recorded.exit_code == 0, recorded.output

        strict = _invoke("contract", "recall", "add a count command", "--repo", "demo-repo")
        loose = _invoke(
            "contract",
            "recall",
            "add a count command for overdue todos",
            "--min-score",
            "0.2",
            "--repo",
            "demo-repo",
        )

        strict_envelope = json.loads(strict.output.strip().splitlines()[-1])
        loose_envelope = json.loads(loose.output.strip().splitlines()[-1])
        assert strict_envelope["entries"] == []
        contents = " ".join(entry["content"] for entry in loose_envelope["entries"])
        assert "overdue" in contents, loose_envelope
