"""Tests for deterministic agent memory A/B evaluation."""

import json
import subprocess
import sys
from pathlib import Path


def test_evaluate_agent_ab_json_smoke():
    script = Path(__file__).resolve().parents[2] / "scripts" / "evaluate_agent_ab.py"

    completed = subprocess.run(
        [sys.executable, str(script), "--json"],
        check=True,
        capture_output=True,
        text=True,
    )

    data = json.loads(completed.stdout)
    summary = data["summary"]

    assert data["benchmark"] == "agent_memory_ab"
    assert data["mode"] == "deterministic_agent_proxy"
    assert data["network"] == "disabled"
    assert summary["cases"] == 5
    assert summary["with_memory"]["task_success_rate"] == 1.0
    assert summary["with_memory"]["wrong_edit_avoidance_rate"] == 1.0
    assert summary["with_memory"]["citation_coverage_rate"] == 1.0
    assert summary["with_memory"]["unknown_abstention_rate"] == 1.0
    assert summary["with_memory"]["mean_labelled_relevance_score"] == 1.0
    assert summary["delta"]["task_success_rate"] > 0
    assert summary["delta"]["relative_risk_reduction"] == 1.0
