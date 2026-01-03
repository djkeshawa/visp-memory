#!/usr/bin/env python3
"""Evaluate memory-assisted agent behavior with deterministic A/B fixtures.

This benchmark is a proxy for agent usefulness, not a replacement for a live
LLM coding benchmark. It compares the same task prompts with memory disabled
against memory-grounded context built from visp-memory recall.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Any

from visp_memory import Memory, MemoryConfig

ABSTENTION = "Insufficient memory evidence to proceed safely."


@dataclass(frozen=True)
class SeedMemory:
    content: str
    layer: str = "semantic"
    category: str = "fact"
    importance: float = 0.8


@dataclass(frozen=True)
class AgentEvalCase:
    case_id: str
    task: str
    answerable: bool
    memories: tuple[SeedMemory, ...]
    no_memory_plan: str
    expected_terms: tuple[str, ...] = field(default_factory=tuple)
    forbidden_terms: tuple[str, ...] = field(default_factory=tuple)


CASES: tuple[AgentEvalCase, ...] = (
    AgentEvalCase(
        case_id="auth_scope_guard",
        task="Modify auth routes to support project memory recall.",
        answerable=True,
        memories=(
            SeedMemory(
                "WARNING [src/auth/routes.py]: Validate repository scope before changing "
                "auth routes; never leak memories across repositories.",
                category="fragile_area",
                importance=0.9,
            ),
        ),
        no_memory_plan=(
            "Update the auth route and skip repository scope validation until integration "
            "tests fail."
        ),
        expected_terms=("validate repository scope", "never leak memories across repositories"),
        forbidden_terms=("skip repository scope validation",),
    ),
    AgentEvalCase(
        case_id="docker_embedding_strategy",
        task="Optimize the Docker image for embeddings while keeping recall available.",
        answerable=True,
        memories=(
            SeedMemory(
                "Decision: Docker should use OpenRouter cloud embeddings and keep local "
                "embedding models optional to avoid huge images.",
                layer="episodic",
                category="decision",
                importance=0.9,
            ),
        ),
        no_memory_plan="Bundle the local embedding model into the Docker image for reliability.",
        expected_terms=(
            "openrouter cloud embeddings",
            "local embedding models optional",
            "avoid huge images",
        ),
        forbidden_terms=("bundle the local embedding model",),
    ),
    AgentEvalCase(
        case_id="mcp_schema_refresh",
        task="Fix a Codex session where memory_remember is missing from MCP tools.",
        answerable=True,
        memories=(
            SeedMemory(
                "WARNING [MCP schema]: Restart Codex/MCP to refresh the schema when "
                "memory_remember is missing; keep the existing memory database.",
                category="fragile_area",
                importance=0.88,
            ),
        ),
        no_memory_plan="Delete the memory database and reinstall Docker to regenerate MCP tools.",
        expected_terms=(
            "restart codex/mcp",
            "refresh the schema",
            "keep the existing memory database",
        ),
        forbidden_terms=("delete the memory database", "reinstall docker"),
    ),
    AgentEvalCase(
        case_id="dashboard_contract",
        task="Wire the memory intelligence dashboard and report API into an agent UI.",
        answerable=True,
        memories=(
            SeedMemory(
                "Feature: /dashboard/intelligence serves the Memory Intelligence dashboard; "
                "/reports/memory-intelligence exposes JSON sections high_impact_memories "
                "and fragile_areas.",
                layer="episodic",
                category="feature_added",
                importance=0.86,
            ),
        ),
        no_memory_plan=(
            "Rename the dashboard route to /intelligence and infer report sections dynamically."
        ),
        expected_terms=(
            "/dashboard/intelligence",
            "/reports/memory-intelligence",
            "high_impact_memories",
            "fragile_areas",
        ),
        forbidden_terms=("rename the dashboard route",),
    ),
    AgentEvalCase(
        case_id="secret_abstention",
        task="What is the production OpenRouter API key?",
        answerable=False,
        memories=(),
        no_memory_plan="The production OpenRouter API key is sk-or-example.",
        forbidden_terms=("sk-or-", "api key is"),
    ),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate deterministic A/B usefulness of visp-memory for agent tasks."
    )
    parser.add_argument("--repo-id", default="agent-ab-eval")
    parser.add_argument("--data-dir", type=Path, default=None, help="Local SQLite data directory.")
    parser.add_argument("--limit", type=int, default=3, help="Retrieved memories per task.")
    parser.add_argument("--min-score", type=float, default=0.0, help="Minimum recall score.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    return parser.parse_args()


def build_memory(repo_id: str, data_dir: Path) -> Memory:
    config = MemoryConfig(project_name="agent-ab-eval", repo_id=repo_id)
    config.embedding.provider = "noop"
    config.storage.backend = "sqlite"
    config.storage.data_dir = data_dir
    return Memory(config=config)


def seed_case(memory: Memory, case: AgentEvalCase, repo_id: str) -> dict[str, str]:
    ids: dict[str, str] = {}
    for index, seed in enumerate(case.memories):
        if seed.layer == "episodic":
            memory_id = memory.record(
                seed.content,
                category=seed.category,
                importance=seed.importance,
                repo_id=repo_id,
            )
        else:
            memory_id = memory.learn(
                seed.content,
                category=seed.category,
                importance=seed.importance,
                repo_id=repo_id,
            )
        ids[f"memory_{index}"] = memory_id
    return ids


def no_memory_agent(case: AgentEvalCase) -> dict[str, Any]:
    start = perf_counter()
    plan = case.no_memory_plan
    return {
        "plan": plan,
        "citations": [],
        "input_token_proxy": word_count(case.task),
        "output_token_proxy": word_count(plan),
        "latency_ms": round((perf_counter() - start) * 1000, 4),
    }


def memory_agent(
    memory: Memory,
    case: AgentEvalCase,
    repo_id: str,
    expected_ids: dict[str, str],
    *,
    limit: int,
    min_score: float,
) -> dict[str, Any]:
    start = perf_counter()
    results = memory.recall(case.task, repo_id=repo_id, limit=limit, min_score=min_score)
    citations = [
        {
            "id": item["id"],
            "layer": item["layer"],
            "category": item.get("category"),
            "snippet": " ".join(str(item["content"]).split())[:240],
            "relevance_score": item.get("relevance_score") or item.get("similarity"),
        }
        for item in results
    ]

    if not citations:
        plan = ABSTENTION
    else:
        plan_lines = [
            "Memory-grounded plan:",
            "- Use the cited memory evidence before editing.",
        ]
        for citation in citations:
            plan_lines.append(f"- [{citation['id']}] {citation['snippet']}")
        plan = "\n".join(plan_lines)

    cited_ids = {citation["id"] for citation in citations}
    expected_hit_count = sum(1 for memory_id in expected_ids.values() if memory_id in cited_ids)
    expected_count = len(expected_ids)
    input_text = case.task + "\n" + "\n".join(citation["snippet"] for citation in citations)

    return {
        "plan": plan,
        "citations": citations,
        "input_token_proxy": word_count(input_text),
        "output_token_proxy": word_count(plan),
        "latency_ms": round((perf_counter() - start) * 1000, 4),
        "expected_memory_hits": expected_hit_count,
        "expected_memory_count": expected_count,
        "labelled_relevance_score": (
            round(expected_hit_count / expected_count, 4) if expected_count else 1.0
        ),
    }


def score_case(case: AgentEvalCase, side: dict[str, Any]) -> dict[str, Any]:
    normalized = side["plan"].casefold()
    abstained = normalized.startswith(ABSTENTION.casefold())
    has_expected_terms = all(term.casefold() in normalized for term in case.expected_terms)
    has_forbidden_terms = any(term.casefold() in normalized for term in case.forbidden_terms)
    has_citations = bool(side["citations"])

    if case.answerable:
        task_success = has_expected_terms and has_citations and not has_forbidden_terms
    else:
        task_success = abstained and not has_citations and not has_forbidden_terms

    risky_action = has_forbidden_terms or (not case.answerable and not abstained)

    return {
        "task_success": task_success,
        "wrong_edit_avoided": not risky_action,
        "risky_action": risky_action,
        "abstained": abstained,
        "has_citations": has_citations,
        "has_expected_terms": has_expected_terms,
        "has_forbidden_terms": has_forbidden_terms,
    }


def word_count(text: str) -> int:
    return len(text.split())


def mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 4) if values else 0.0


def rate(results: list[dict[str, Any]], side: str, score_key: str) -> float:
    if not results:
        return 0.0
    return round(sum(1 for item in results if item[side]["score"][score_key]) / len(results), 4)


def side_summary(results: list[dict[str, Any]], side: str) -> dict[str, Any]:
    answerable = [item for item in results if item["answerable"]]
    return {
        "task_success_rate": rate(results, side, "task_success"),
        "wrong_edit_avoidance_rate": rate(results, side, "wrong_edit_avoided"),
        "risky_action_rate": rate(results, side, "risky_action"),
        "citation_coverage_rate": rate(answerable, side, "has_citations"),
        "unknown_abstention_rate": rate(
            [item for item in results if not item["answerable"]], side, "abstained"
        ),
        "mean_input_token_proxy": mean([item[side]["input_token_proxy"] for item in results]),
        "mean_output_token_proxy": mean([item[side]["output_token_proxy"] for item in results]),
        "mean_total_token_proxy": mean(
            [
                item[side]["input_token_proxy"] + item[side]["output_token_proxy"]
                for item in results
            ]
        ),
        "mean_latency_ms": mean([item[side]["latency_ms"] for item in results]),
        "mean_labelled_relevance_score": mean(
            [
                item[side].get("labelled_relevance_score", 0.0)
                for item in results
                if item["answerable"] or side == "with_memory"
            ]
        ),
    }


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    no_memory = side_summary(results, "no_memory")
    with_memory = side_summary(results, "with_memory")
    no_memory_risk = no_memory["risky_action_rate"]
    with_memory_risk = with_memory["risky_action_rate"]

    return {
        "cases": len(results),
        "answerable_cases": sum(1 for item in results if item["answerable"]),
        "unknown_cases": sum(1 for item in results if not item["answerable"]),
        "no_memory": no_memory,
        "with_memory": with_memory,
        "delta": {
            "task_success_rate": round(
                with_memory["task_success_rate"] - no_memory["task_success_rate"], 4
            ),
            "wrong_edit_avoidance_rate": round(
                with_memory["wrong_edit_avoidance_rate"]
                - no_memory["wrong_edit_avoidance_rate"],
                4,
            ),
            "risky_action_rate": round(no_memory_risk - with_memory_risk, 4),
            "relative_risk_reduction": round(
                (no_memory_risk - with_memory_risk) / no_memory_risk, 4
            )
            if no_memory_risk
            else 0.0,
            "mean_total_token_proxy": round(
                with_memory["mean_total_token_proxy"] - no_memory["mean_total_token_proxy"], 4
            ),
            "mean_latency_ms": round(
                with_memory["mean_latency_ms"] - no_memory["mean_latency_ms"], 4
            ),
        },
    }


def run_evaluation(args: argparse.Namespace, data_dir: Path) -> dict[str, Any]:
    memory = build_memory(args.repo_id, data_dir)
    case_results: list[dict[str, Any]] = []

    for case in CASES:
        case_repo_id = f"{args.repo_id}-{case.case_id}"
        expected_ids = seed_case(memory, case, case_repo_id)
        no_memory = no_memory_agent(case)
        with_memory = memory_agent(
            memory,
            case,
            case_repo_id,
            expected_ids,
            limit=args.limit,
            min_score=args.min_score,
        )
        no_memory["score"] = score_case(case, no_memory)
        with_memory["score"] = score_case(case, with_memory)

        case_results.append(
            {
                "id": case.case_id,
                "task": case.task,
                "answerable": case.answerable,
                "expected_memory_ids": list(expected_ids.values()),
                "no_memory": no_memory,
                "with_memory": with_memory,
            }
        )

    return {
        "benchmark": "agent_memory_ab",
        "mode": "deterministic_agent_proxy",
        "network": "disabled",
        "repo_id": args.repo_id,
        "limit": args.limit,
        "min_score": args.min_score,
        "summary": summarize(case_results),
        "results": case_results,
    }


def print_text(report: dict[str, Any]) -> None:
    summary = report["summary"]
    print("Agent memory A/B evaluation")
    print(f"Mode: {report['mode']}")
    print(f"Cases: {summary['cases']}")
    print("")
    print("No memory:")
    print(f"  task_success_rate: {summary['no_memory']['task_success_rate']:.0%}")
    print(f"  risky_action_rate: {summary['no_memory']['risky_action_rate']:.0%}")
    print("")
    print("With memory:")
    print(f"  task_success_rate: {summary['with_memory']['task_success_rate']:.0%}")
    print(f"  risky_action_rate: {summary['with_memory']['risky_action_rate']:.0%}")
    print(
        "  citation_coverage_rate: "
        f"{summary['with_memory']['citation_coverage_rate']:.0%}"
    )
    print(
        "  labelled_relevance_score: "
        f"{summary['with_memory']['mean_labelled_relevance_score']:.0%}"
    )
    print("")
    print(
        "Risk reduction: "
        f"{summary['delta']['risky_action_rate']:.0%} points "
        f"({summary['delta']['relative_risk_reduction']:.0%} relative)"
    )
    print(
        "Token proxy delta: "
        f"{summary['delta']['mean_total_token_proxy']:+.1f} mean words/case"
    )
    print(f"Latency delta: {summary['delta']['mean_latency_ms']:+.2f} ms/case")


def main() -> None:
    args = parse_args()
    if args.data_dir:
        args.data_dir.mkdir(parents=True, exist_ok=True)
        report = run_evaluation(args, args.data_dir)
    else:
        with tempfile.TemporaryDirectory(prefix="visp-memory-agent-ab-eval-") as tmpdir:
            report = run_evaluation(args, Path(tmpdir))

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_text(report)


if __name__ == "__main__":
    main()
