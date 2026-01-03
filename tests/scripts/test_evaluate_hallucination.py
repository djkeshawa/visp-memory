"""Tests for hallucination grounding evaluation utility."""

import json
import subprocess
import sys
from pathlib import Path


def test_evaluate_hallucination_json_smoke():
    script = Path(__file__).resolve().parents[2] / "scripts" / "evaluate_hallucination.py"

    completed = subprocess.run(
        [sys.executable, str(script), "--json"],
        check=True,
        capture_output=True,
        text=True,
    )

    data = json.loads(completed.stdout)
    summary = data["summary"]

    assert data["benchmark"] == "hallucination_grounding"
    assert data["mode"] == "deterministic_retrieval_proxy"
    assert summary["cases"] == 4
    assert summary["with_memory"]["accuracy"] == 1.0
    assert summary["with_memory"]["unsupported_or_false_rate"] == 0.0
    assert summary["with_memory"]["citation_coverage_rate"] == 1.0
    assert summary["with_memory"]["unknown_abstention_rate"] == 1.0
    assert summary["reduction"]["unsupported_or_false_rate_delta"] > 0
