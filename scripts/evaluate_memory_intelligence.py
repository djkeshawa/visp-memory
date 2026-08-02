#!/usr/bin/env python3
"""Evaluate local memory intelligence behavior with deterministic fixtures."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from time import perf_counter
from typing import Any

from visp_memory import Memory, MemoryConfig
from visp_memory.capture.git import CaptureManifest, capture_content_hash
from visp_memory.core.reporting import MemoryIntelligenceReporter
from visp_memory.core.trust import Provenance, provenance_tag
from visp_memory.recall.graph import GraphRecall


def _store(storage, content, **kwargs):
    if kwargs.get("layer") == "semantic":
        kwargs["evidence_ids"] = [
            storage.store_evidence(content, repo_id=kwargs["repo_id"])
        ]
    return storage.store_memory(content, **kwargs)

EVAL_CASES: tuple[dict[str, str], ...] = (
    {
        "query": "memory intelligence report JSON dashboard contract",
        "expected_key": "report_contract",
    },
    {
        "query": "relationship evidence path for dashboard graph",
        "expected_key": "graph_evidence",
    },
    {
        "query": "freshness stale decay candidate",
        "expected_key": "freshness_memory",
    },
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate deterministic local memory intelligence fixtures."
    )
    parser.add_argument("--repo-id", default="memory-intelligence-eval")
    parser.add_argument("--data-dir", type=Path, default=None, help="Local SQLite data directory.")
    parser.add_argument("--limit", type=int, default=3, help="Top-k recall limit.")
    parser.add_argument("--token-budget", type=int, default=120, help="Graph trace token budget.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    return parser.parse_args()


def build_memory(repo_id: str, data_dir: Path) -> Memory:
    config = MemoryConfig(project_name="memory-intelligence-eval", repo_id=repo_id)
    config.embedding.provider = "noop"
    config.storage.backend = "sqlite"
    config.storage.data_dir = data_dir
    return Memory(config=config)


def seed_fixture(memory: Memory, repo_id: str) -> dict[str, str]:
    storage = memory._storage
    derived_tags = [provenance_tag(Provenance.DERIVED)]
    ids = {
        "report_contract": _store(storage,
            "Memory intelligence report JSON exposes deterministic dashboard sections.",
            layer="semantic",
            repo_id=repo_id,
            category="fact",
            importance=0.9,
            tags=derived_tags,
            auto_link=False,
        ),
        "graph_evidence": _store(storage,
            "Relationship evidence paths explain why graph findings are relevant.",
            layer="semantic",
            repo_id=repo_id,
            category="fact",
            importance=0.82,
            tags=derived_tags,
            auto_link=False,
        ),
        "freshness_memory": _store(storage,
            "Freshness manifest should keep unchanged capture inputs from creating duplicates.",
            layer="episodic",
            repo_id=repo_id,
            category="session",
            importance=0.78,
            tags=derived_tags,
            auto_link=False,
        ),
        "conflict_candidate": _store(storage,
            "Conflict candidate: ranking can ignore relationship evidence paths.",
            layer="semantic",
            repo_id=repo_id,
            category="fact",
            importance=0.55,
            tags=derived_tags,
            metadata={"conflict": {"reason": "Fixture contradiction candidate."}},
            quality_flags=["contradiction"],
            auto_link=False,
        ),
    }

    ids["stale_intent"] = memory.goal(
        "Finish stale dashboard migration cleanup",
        priority=2,
        repo_id=repo_id,
    )

    _age_intent(memory, ids["stale_intent"], "2020-01-01T00:00:00")
    _age_memory_access(memory, ids["freshness_memory"], "2020-01-01T00:00:00")
    _seed_relationships(storage, ids)
    ids["capture_replay"] = _replay_unchanged_capture(memory, repo_id)
    return ids


def _seed_relationships(storage: Any, ids: dict[str, str]) -> None:
    storage.add_relationship(
        ids["report_contract"],
        ids["graph_evidence"],
        "supports",
        strength=0.9,
        evidence={
            "confidence": "observed",
            "confidence_score": 0.95,
            "source": "evaluation_fixture",
            "source_file": "scripts/evaluate_memory_intelligence.py",
            "reason": "The graph evidence fixture supports the report contract fixture.",
        },
    )
    storage.add_relationship(
        ids["graph_evidence"],
        ids["freshness_memory"],
        "explains",
        strength=0.85,
        evidence={
            "confidence": "observed",
            "confidence_score": 0.9,
            "source": "evaluation_fixture",
            "source_file": "scripts/evaluate_memory_intelligence.py",
            "reason": "The freshness fixture is explained through evidence-backed graph recall.",
        },
    )


def _age_intent(memory: Memory, intent_id: str, timestamp: str) -> None:
    with memory._storage._get_db() as conn:
        conn.execute(
            "UPDATE intents SET created_at = ?, updated_at = ? WHERE id = ?",
            (timestamp, timestamp, intent_id),
        )
        conn.commit()


def _age_memory_access(memory: Memory, memory_id: str, timestamp: str) -> None:
    with memory._storage._get_db() as conn:
        conn.execute("UPDATE memories SET accessed_at = ? WHERE id = ?", (timestamp, memory_id))
        conn.commit()


def _replay_unchanged_capture(memory: Memory, repo_id: str) -> str:
    manifest = CaptureManifest(memory)
    source = f"{repo_id}:conversation"
    payload = {
        "source": source,
        "content": "Repeated capture input for memory intelligence freshness.",
    }
    content_hash = capture_content_hash(payload)
    created_id = ""

    for _ in range(2):
        status, _entry = manifest.check("conversation", source, content_hash)
        if status == "unchanged":
            continue
        created_id = memory._storage.store_memory(
            payload["content"],
            layer="episodic",
            repo_id=repo_id,
            category="session",
            importance=0.7,
            tags=[provenance_tag(Provenance.ASSISTED)],
            auto_link=False,
        )
        manifest.record("conversation", source, content_hash, [created_id], status=status)

    return created_id


def recall_precision_at_k(
    memory: Memory,
    ids: dict[str, str],
    repo_id: str,
    limit: int,
) -> tuple[float, list[dict[str, Any]]]:
    results = []
    hits = 0
    for case in EVAL_CASES:
        recalled = memory.recall(case["query"], repo_id=repo_id, limit=limit, min_score=0)
        recalled_ids = [item["id"] for item in recalled]
        expected_id = ids[case["expected_key"]]
        hit = expected_id in recalled_ids[:limit]
        hits += int(hit)
        results.append(
            {
                "query": case["query"],
                "expected_id": expected_id,
                "recalled_ids": recalled_ids[:limit],
                "hit": hit,
            }
        )

    return round(hits / len(EVAL_CASES), 4), results


def evidence_path_coverage(memory: Memory, ids: dict[str, str], repo_id: str) -> float:
    path = GraphRecall(memory._storage).path(
        ids["report_contract"],
        ids["freshness_memory"],
        repo_id=repo_id,
        max_hops=2,
        token_budget=500,
    )
    if not path["edges"]:
        return 0.0
    return round(
        sum(1 for edge in path["edges"] if edge.get("evidence")) / len(path["edges"]),
        4,
    )


def graph_token_budget_metrics(
    memory: Memory,
    repo_id: str,
    token_budget: int,
    limit: int,
) -> dict[str, Any]:
    trace = GraphRecall(memory._storage).trace(
        "memory intelligence dashboard graph evidence freshness",
        repo_id=repo_id,
        depth=2,
        token_budget=token_budget,
        limit=limit,
    )
    used = _estimate_graph_tokens(trace)
    budget = trace["limits"]["token_budget"]
    return {
        "token_budget_limit": budget,
        "token_budget_used": used,
        "token_budget_use_ratio": round(used / budget, 4),
        "omitted": trace["omitted"],
    }


def _estimate_graph_tokens(trace: dict[str, Any]) -> int:
    used = 0
    for node in trace["nodes"]:
        used += len(str(node.get("content", "")).split()) + 10
    for edge in trace["edges"]:
        used += len(str(edge.get("reason", "")).split()) + 8
    return used


def duplicate_rate_after_capture(memory: Memory, repo_id: str) -> float:
    memories = memory._storage.list_memories(repo_id=repo_id, status="active", limit=1000)
    normalized_counts: dict[str, int] = {}
    for item in memories:
        normalized = " ".join(str(item.get("content", "")).casefold().split())
        if normalized:
            normalized_counts[normalized] = normalized_counts.get(normalized, 0) + 1

    duplicate_rows = sum(count - 1 for count in normalized_counts.values() if count > 1)
    return round(duplicate_rows / max(1, len(memories)), 4)


def stale_surfacing_rate(report: dict[str, Any], stale_intent_id: str) -> float:
    stale_items = report["sections"]["stale_intents"]["items"]
    if not stale_items:
        return 0.0
    return 1.0 if any(item["id"] == stale_intent_id for item in stale_items) else 0.0


def run_evaluation(args: argparse.Namespace, data_dir: Path) -> dict[str, Any]:
    memory = build_memory(args.repo_id, data_dir)
    ids = seed_fixture(memory, args.repo_id)

    recall_precision, recall_cases = recall_precision_at_k(
        memory,
        ids,
        args.repo_id,
        max(1, args.limit),
    )
    path_coverage = evidence_path_coverage(memory, ids, args.repo_id)
    token_metrics = graph_token_budget_metrics(memory, args.repo_id, args.token_budget, args.limit)

    reporter = MemoryIntelligenceReporter(memory._storage)
    start = perf_counter()
    report = reporter.generate(repo_id=args.repo_id, limit=10)
    report_generation_ms = round((perf_counter() - start) * 1000, 3)

    metrics = {
        "recall_precision_at_3": recall_precision,
        "evidence_path_coverage": path_coverage,
        "duplicate_rate": duplicate_rate_after_capture(memory, args.repo_id),
        "token_budget_limit": token_metrics["token_budget_limit"],
        "token_budget_used": token_metrics["token_budget_used"],
        "token_budget_use_ratio": token_metrics["token_budget_use_ratio"],
        "stale_surfacing_rate": stale_surfacing_rate(report, ids["stale_intent"]),
        "report_generation_ms": report_generation_ms,
    }

    return {
        "benchmark": "memory_intelligence_local",
        "mode": "deterministic_local_fixture",
        "network": "disabled",
        "repo_id": args.repo_id,
        "metrics": metrics,
        "cases": {
            "recall": recall_cases,
            "graph_omitted": token_metrics["omitted"],
            "report_sections": sorted(report["sections"].keys()),
        },
    }


def print_text(report: dict[str, Any]) -> None:
    metrics = report["metrics"]
    print("Memory intelligence evaluation")
    print(f"Mode: {report['mode']}")
    print(f"Network: {report['network']}")
    print(f"Recall precision@3: {metrics['recall_precision_at_3']:.0%}")
    print(f"Evidence path coverage: {metrics['evidence_path_coverage']:.0%}")
    print(f"Duplicate rate: {metrics['duplicate_rate']:.0%}")
    print(
        "Token budget: "
        f"{metrics['token_budget_used']}/{metrics['token_budget_limit']} "
        f"({metrics['token_budget_use_ratio']:.0%})"
    )
    print(f"Stale surfacing rate: {metrics['stale_surfacing_rate']:.0%}")
    print(f"Report generation: {metrics['report_generation_ms']:.3f}ms")


def main() -> None:
    args = parse_args()
    if args.data_dir:
        args.data_dir.mkdir(parents=True, exist_ok=True)
        report = run_evaluation(args, args.data_dir)
    else:
        with tempfile.TemporaryDirectory(prefix="visp-memory-intelligence-eval-") as tmpdir:
            report = run_evaluation(args, Path(tmpdir))

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_text(report)


if __name__ == "__main__":
    main()
