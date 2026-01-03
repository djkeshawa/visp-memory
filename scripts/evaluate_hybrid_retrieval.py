#!/usr/bin/env python3
"""Evaluate hybrid associative retrieval against direct retrieval on fixed fixtures."""

from __future__ import annotations

import json
import math
import statistics
import tempfile
import time
from pathlib import Path

from visp_memory.core.hybrid_retrieval import HybridRetriever
from visp_memory.core.ranking import rank_memory_results
from visp_memory.core.storage import LocalStorage


def _direct(storage, query: str, repo_id: str, limit: int = 10) -> list[dict]:
    candidates = {}
    for layer in ("intent", "semantic", "episodic", "raw"):
        for memory in storage.search_memories(
            query=query,
            repo_id=repo_id,
            layer=layer,
            status="active",
            limit=40,
        ):
            candidates[memory["id"]] = memory
    return rank_memory_results(list(candidates.values()), query=query, limit=limit)


def _recall_at(results: list[dict], expected: set[str], limit: int) -> float:
    found = {item["id"] for item in results[:limit]}
    return len(found.intersection(expected)) / max(len(expected), 1)


def _reciprocal_rank(results: list[dict], expected: set[str]) -> float:
    for index, item in enumerate(results, start=1):
        if item["id"] in expected:
            return 1.0 / index
    return 0.0


def _ndcg_at(results: list[dict], expected: set[str], limit: int) -> float:
    dcg = sum(
        1.0 / math.log2(index + 1)
        for index, item in enumerate(results[:limit], start=1)
        if item["id"] in expected
    )
    ideal = sum(
        1.0 / math.log2(index + 1)
        for index in range(1, min(len(expected), limit) + 1)
    )
    return dcg / ideal if ideal else 0.0


def evaluate() -> dict:
    with tempfile.TemporaryDirectory(prefix="visp-memory-hybrid-eval-") as tmpdir:
        storage = LocalStorage(Path(tmpdir))
        repo_id = "hybrid-eval"
        oauth = storage.store_memory(
            "OAuth refresh token rotation policy",
            layer="semantic",
            repo_id=repo_id,
            auto_link=False,
        )
        lineage = storage.store_memory(
            "Token families retain parent child lineage",
            layer="semantic",
            repo_id=repo_id,
            auto_link=False,
        )
        replay = storage.store_memory(
            "Invalidate all descendants after credential replay detection",
            layer="semantic",
            repo_id=repo_id,
            auto_link=False,
        )
        migration = storage.store_memory(
            "Database migrations use an advisory lock with a thirty second timeout",
            layer="semantic",
            repo_id=repo_id,
            auto_link=False,
        )
        auth_file = storage.store_memory(
            "Configured secrets require constant-time comparison",
            layer="semantic",
            repo_id=repo_id,
            metadata={"files": ["src/auth.py"]},
            auto_link=False,
        )
        hub = storage.store_memory(
            "General project conventions",
            layer="semantic",
            repo_id=repo_id,
            auto_link=False,
        )
        storage.add_relationship(oauth, lineage, "supports")
        storage.add_relationship(lineage, replay, "supports")
        storage.add_relationship(oauth, hub, "related_to", evidence={"confidence": "inferred"})
        for index in range(8):
            noise = storage.store_memory(
                f"Routine unrelated note number {index}",
                layer="episodic",
                repo_id=repo_id,
                auto_link=False,
            )
            storage.add_relationship(hub, noise, "related_to", evidence={"confidence": "inferred"})

        cases = [
            {
                "query": "How should OAuth refresh token rotation handle replay?",
                "expected": {oauth, replay},
                "files": [],
            },
            {
                "query": "What timeout protects database migrations?",
                "expected": {migration},
                "files": [],
            },
            {
                "query": "Review credential handling",
                "expected": {auth_file},
                "files": ["src/auth.py"],
            },
        ]
        direct_results = []
        hybrid_results = []
        durations_ms = []
        retriever = HybridRetriever(storage)
        for case in cases:
            direct_results.append(_direct(storage, case["query"], repo_id))
            started = time.perf_counter()
            hybrid_results.append(
                retriever.retrieve(
                    case["query"],
                    repo_id=repo_id,
                    files=case["files"],
                    limit=10,
                )
            )
            durations_ms.append((time.perf_counter() - started) * 1000)

        direct_recall = statistics.mean(
            _recall_at(result, case["expected"], 10)
            for result, case in zip(direct_results, cases)
        )
        hybrid_recall = statistics.mean(
            _recall_at(result, case["expected"], 10)
            for result, case in zip(hybrid_results, cases)
        )
        direct_mrr = statistics.mean(
            _reciprocal_rank(result, case["expected"])
            for result, case in zip(direct_results, cases)
        )
        hybrid_mrr = statistics.mean(
            _reciprocal_rank(result, case["expected"])
            for result, case in zip(hybrid_results, cases)
        )
        hybrid_ndcg = statistics.mean(
            _ndcg_at(result, case["expected"], 10)
            for result, case in zip(hybrid_results, cases)
        )
        p95_index = max(0, math.ceil(len(durations_ms) * 0.95) - 1)
        p95_latency_ms = sorted(durations_ms)[p95_index]
        direct_oauth_ids = {item["id"] for item in direct_results[0]}
        hybrid_oauth_ids = {item["id"] for item in hybrid_results[0]}
        metrics = {
            "cases": len(cases),
            "direct_recall_at_10": round(direct_recall, 4),
            "hybrid_recall_at_10": round(hybrid_recall, 4),
            "direct_mrr": round(direct_mrr, 4),
            "hybrid_mrr": round(hybrid_mrr, 4),
            "hybrid_ndcg_at_10": round(hybrid_ndcg, 4),
            "p95_latency_ms": round(p95_latency_ms, 2),
            "multi_hop_recovered": replay not in direct_oauth_ids and replay in hybrid_oauth_ids,
            "hub_did_not_override_seed": hybrid_results[0][0]["id"] == oauth,
        }
        metrics["passed"] = bool(
            hybrid_recall > direct_recall
            and hybrid_mrr >= direct_mrr
            and hybrid_ndcg >= 0.9
            and p95_latency_ms < 500
            and metrics["multi_hop_recovered"]
            and metrics["hub_did_not_override_seed"]
        )
        return metrics


def main() -> int:
    metrics = evaluate()
    print(json.dumps(metrics, indent=2, sort_keys=True))
    return 0 if metrics["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
