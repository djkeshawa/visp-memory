"""Tests for local memory intelligence evaluation fixtures."""

import json
import subprocess
import sys
from pathlib import Path


def test_evaluate_memory_intelligence_json_matches_baseline():
    root = Path(__file__).resolve().parents[2]
    script = root / "scripts" / "evaluate_memory_intelligence.py"
    baseline_path = root / "docs" / "development" / "MEMORY_INTELLIGENCE_BASELINE.json"
    baseline = json.loads(baseline_path.read_text())

    completed = subprocess.run(
        [sys.executable, str(script), "--json"],
        check=True,
        capture_output=True,
        text=True,
    )

    data = json.loads(completed.stdout)
    metrics = data["metrics"]

    assert data["benchmark"] == baseline["benchmark"]
    assert data["mode"] == baseline["mode"]
    assert data["network"] == "disabled"
    for key in baseline["stable_metric_keys"]:
        assert metrics[key] == baseline["metrics"][key]
    assert metrics["report_generation_ms"] <= baseline["thresholds"]["report_generation_ms_max"]
    assert data["cases"]["graph_omitted"] == []
    assert "stale_intents" in data["cases"]["report_sections"]
