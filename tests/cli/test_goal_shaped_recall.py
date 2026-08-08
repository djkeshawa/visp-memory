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
        recorded = _invoke("record", "The deploy pipeline uses blue-green cutover", "--repo", "demo-repo")
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
