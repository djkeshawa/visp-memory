"""Tests for benchmark utility."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


def load_benchmark_module():
    script = Path(__file__).resolve().parents[2] / "scripts" / "benchmark_memory.py"
    spec = importlib.util.spec_from_file_location("benchmark_memory", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def test_benchmark_accepts_arcadedb_backend_choice(monkeypatch, tmp_path):
    benchmark = load_benchmark_module()
    monkeypatch.setattr(
        sys,
        "argv",
        ["benchmark_memory.py", "--backend", "arcadedb", "--items", "1"],
    )

    args = benchmark.parse_args()
    config = benchmark.build_config(args, tmp_path)

    assert args.backend == "arcadedb"
    assert config.storage.backend == "arcadedb"
    assert config.storage.data_dir == tmp_path
