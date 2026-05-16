"""Tests for benchmark utility."""

import json
import subprocess
import sys
from pathlib import Path


def test_benchmark_memory_json_smoke():
    script = Path(__file__).resolve().parents[2] / "scripts" / "benchmark_memory.py"

    completed = subprocess.run(
        [sys.executable, str(script), "--items", "10", "--json"],
        check=True,
        capture_output=True,
        text=True,
    )

    data = json.loads(completed.stdout)
    assert data["backend"] == "sqlite"
    assert data["items"] == 10
    assert data["operations"]["insert"]["result_count"] == 10
    assert data["operations"]["list"]["result_count"] == 10
    assert data["operations"]["recall"]["result_count"] > 0
    assert data["operations"]["import"]["result_count"] == 10
