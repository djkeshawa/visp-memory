#!/usr/bin/env python3
"""Benchmark core memory operations with deterministic local defaults."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import tempfile
from pathlib import Path
from time import perf_counter
from typing import Any, Callable

from llm_memory import Memory, MemoryConfig


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark LLM Memory core operations.")
    parser.add_argument("--items", type=int, default=100, help="Number of memories to create.")
    parser.add_argument("--backend", choices=("sqlite", "arcadedb", "neo4j"), default="sqlite")
    parser.add_argument("--repo-id", default="benchmark-repo")
    parser.add_argument("--data-dir", type=Path, default=None, help="Storage data directory.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    return parser.parse_args()


def build_config(args: argparse.Namespace, data_dir: Path) -> MemoryConfig:
    config = MemoryConfig(project_name="benchmark", repo_id=args.repo_id)
    config.embedding.provider = "noop"
    config.storage.backend = args.backend
    config.storage.data_dir = data_dir

    if args.backend == "neo4j":
        config.storage.neo4j_uri = os.getenv("NEO4J_URI", config.storage.neo4j_uri)
        config.storage.neo4j_user = os.getenv("NEO4J_USER", config.storage.neo4j_user)
        config.storage.neo4j_password = os.getenv("NEO4J_PASSWORD", config.storage.neo4j_password)
        if not config.storage.neo4j_password:
            raise SystemExit("NEO4J_PASSWORD is required for --backend neo4j")

    return config


def time_call(operation: Callable[[], Any], runs: int = 1) -> dict[str, Any]:
    durations = []
    result = None
    for _ in range(runs):
        start = perf_counter()
        result = operation()
        durations.append(perf_counter() - start)

    return {
        "seconds": durations[-1],
        "mean_seconds": statistics.mean(durations),
        "runs": runs,
        "result_count": len(result) if hasattr(result, "__len__") else None,
    }


def run_benchmark(args: argparse.Namespace, data_dir: Path) -> dict[str, Any]:
    if args.items < 1:
        raise SystemExit("--items must be greater than zero")

    memory = Memory(config=build_config(args, data_dir))
    export_path = data_dir / "benchmark-export.json"
    import_dir = data_dir / "imported"

    metrics: dict[str, Any] = {
        "backend": args.backend,
        "items": args.items,
        "repo_id": args.repo_id,
        "operations": {},
    }

    def insert_items() -> list[str]:
        ids = []
        for index in range(args.items):
            if index % 2:
                ids.append(memory.learn(f"Benchmark knowledge item {index}", category="fact"))
            else:
                ids.append(memory.record(f"Benchmark event item {index}", category="note"))
        return ids

    metrics["operations"]["insert"] = time_call(insert_items)
    metrics["operations"]["list"] = time_call(
        lambda: memory._storage.list_memories(repo_id=args.repo_id, limit=args.items * 2),
        runs=5,
    )
    metrics["operations"]["recall"] = time_call(
        lambda: memory.recall("Benchmark item", limit=min(args.items, 20)),
        runs=5,
    )
    metrics["operations"]["context"] = time_call(lambda: memory.context(format="json"), runs=3)
    metrics["operations"]["export"] = time_call(lambda: memory.export(export_path), runs=3)

    def import_export() -> list[dict[str, Any]]:
        import_config = build_config(args, import_dir)
        import_config.repo_id = f"{args.repo_id}-imported"
        imported = Memory(config=import_config)
        imported.import_memories(export_path)
        return imported._storage.list_memories(repo_id=args.repo_id, limit=args.items * 2)

    metrics["operations"]["import"] = time_call(import_export)
    return metrics


def print_text(metrics: dict[str, Any]) -> None:
    print(f"Backend: {metrics['backend']}")
    print(f"Items: {metrics['items']}")
    for name, data in metrics["operations"].items():
        count = data["result_count"]
        suffix = f", result_count={count}" if count is not None else ""
        print(f"{name}: {data['mean_seconds']:.4f}s mean over {data['runs']} run(s){suffix}")


def main() -> None:
    args = parse_args()
    if args.data_dir:
        args.data_dir.mkdir(parents=True, exist_ok=True)
        metrics = run_benchmark(args, args.data_dir)
    else:
        with tempfile.TemporaryDirectory(prefix="llm-memory-benchmark-") as tmpdir:
            metrics = run_benchmark(args, Path(tmpdir))

    if args.json:
        print(json.dumps(metrics, indent=2))
    else:
        print_text(metrics)


if __name__ == "__main__":
    main()
