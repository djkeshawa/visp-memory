"""Regression tests for the oracle-gap benchmark.

These pin the *properties* the injection policy claims, not exact scores. If a future
change to ranking or thresholds makes the policy chattier or less precise, this fails
before the claim reaches a README.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "evaluate_oracle_gap.py"


@pytest.fixture(scope="module")
def report():
    """Run the script the way CI and a user would, rather than importing internals."""
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_human_readable_output_runs(report):
    completed = subprocess.run(
        [sys.executable, str(SCRIPT)], check=True, capture_output=True, text=True
    )
    assert "Oracle gap closed" in completed.stdout
    # The caveat must survive: this is a selection proxy, not a live-agent result.
    assert "not how many issues an agent then resolved" in completed.stdout


def _strategy(report, name):
    return next(s for s in report["strategies"] if s["strategy"].startswith(name))


class TestBenchmarkShape:
    def test_covers_answerable_and_unanswerable_cases(self, report):
        """A benchmark with only answerable tasks cannot detect over-injection."""
        assert report["answerable_cases"] >= 3
        assert report["cases"] - report["answerable_cases"] >= 3

    def test_oracle_is_a_perfect_upper_bound(self, report):
        oracle = _strategy(report, "oracle")
        assert oracle["precision"] == 1.0
        assert oracle["recall"] == 1.0


class TestPolicyBeatsNaiveRetrieval:
    def test_policy_is_far_more_precise(self, report):
        policy = _strategy(report, "policy")
        unfiltered = _strategy(report, "unfiltered")
        assert policy["precision"] >= 0.9
        assert policy["precision"] > unfiltered["precision"] * 2

    def test_policy_stays_silent_when_nothing_can_help(self, report):
        """The property naive retrieval cannot have: knowing when to say nothing."""
        policy = _strategy(report, "policy")
        assert policy["silence_accuracy"] == 1.0
        assert policy["false_alarms"] == 0

    def test_naive_retrieval_raises_false_alarms(self, report):
        """Guards the comparison itself: if the baseline stopped over-injecting, this
        benchmark would no longer be measuring anything."""
        unfiltered = _strategy(report, "unfiltered")
        assert unfiltered["false_alarms"] >= 2

    def test_policy_costs_far_fewer_tokens(self, report):
        policy = _strategy(report, "policy")
        unfiltered = _strategy(report, "unfiltered")
        assert policy["mean_injected_tokens"] < unfiltered["mean_injected_tokens"] / 4

    def test_closes_most_of_the_oracle_gap(self, report):
        assert report["oracle_gap_closed"] >= 0.5


class TestHonestReporting:
    def test_recall_cost_of_abstaining_is_reported(self, report):
        """Precision bought by abstaining costs recall, and the report must show it
        rather than quietly presenting precision alone."""
        policy = _strategy(report, "policy")
        assert "recall" in policy
        assert policy["recall"] < 1.0, "if recall were perfect the fixtures are too easy"

    def test_per_case_records_why_nothing_was_injected(self, report):
        silent = [c for c in report["per_case"] if not c["policy_selected"]]
        assert silent, "expected at least one abstention to explain"
        assert all(c["policy_reason"] for c in silent)
