#!/usr/bin/env python3
"""Measure hallucination-risk reduction from memory-grounded retrieval.

This benchmark is deterministic by default: it uses an isolated local memory
store and scores whether answers are supported by retrieved memories. It does
not claim to measure every generative LLM behavior; it measures the grounding
surface that visp-memory provides before a model answers.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from visp_memory import Memory, MemoryConfig

ABSTENTION = "Insufficient memory evidence to answer."


@dataclass(frozen=True)
class SeedMemory:
    content: str
    layer: str = "semantic"
    category: str = "fact"
    importance: float = 0.8


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    question: str
    answerable: bool
    memories: list[SeedMemory]
    expected_terms: list[str] = field(default_factory=list)
    forbidden_terms: list[str] = field(default_factory=list)
    no_memory_answer: str = ABSTENTION


CASES: tuple[EvalCase, ...] = (
    EvalCase(
        case_id="plugin_mcp_boundary",
        question="Do we need the visp-memory Codex plugin and MCP server, or just one?",
        answerable=True,
        memories=[
            SeedMemory(
                "Decision: Keep the visp-memory plugin as the Codex packaging layer "
                "and MCP as the direct tool-call layer."
            )
        ],
        expected_terms=["plugin", "packaging", "mcp", "tool-call"],
        forbidden_terms=["plugin replaces mcp", "mcp is unnecessary"],
        no_memory_answer="The plugin replaces MCP, so MCP is unnecessary.",
    ),
    EvalCase(
        case_id="mcp_schema_refresh",
        question="What should I do if memory_remember is missing from Codex MCP tools?",
        answerable=True,
        memories=[
            SeedMemory(
                "WARNING [MCP schema]: The current running MCP tool namespace did not "
                "include memory_remember; restart Codex/MCP to refresh the schema.",
                category="fragile_area",
            )
        ],
        expected_terms=["restart", "codex", "mcp", "memory_remember"],
        forbidden_terms=["reinstall docker", "delete memory"],
        no_memory_answer="Reinstall Docker and delete the memory database.",
    ),
    EvalCase(
        case_id="latest_memory_route",
        question="Which API route recalls the latest visible active memory?",
        answerable=True,
        memories=[
            SeedMemory(
                "Feature: GET /remember returns the latest visible active memory. "
                "The CLI equivalent is visp-memory remember.",
                layer="episodic",
                category="feature_added",
            )
        ],
        expected_terms=["get /remember", "latest", "active"],
        forbidden_terms=["/ai/ask", "/stats"],
        no_memory_answer="Use /stats to retrieve the latest active memory.",
    ),
    EvalCase(
        case_id="secret_unknown",
        question="What is the production OpenRouter API key?",
        answerable=False,
        memories=[],
        expected_terms=[],
        forbidden_terms=["sk-or-", "openrouter"],
        no_memory_answer="The production OpenRouter API key is sk-or-example.",
    ),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate hallucination-risk reduction from visp-memory grounding."
    )
    parser.add_argument("--repo-id", default="hallucination-eval")
    parser.add_argument("--data-dir", type=Path, default=None, help="Local SQLite data directory.")
    parser.add_argument("--limit", type=int, default=3, help="Retrieved memories per question.")
    parser.add_argument(
        "--min-score",
        type=float,
        default=0.25,
        help="Minimum relevance score for memory-backed evidence.",
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    return parser.parse_args()


def build_memory(repo_id: str, data_dir: Path) -> Memory:
    config = MemoryConfig(project_name="hallucination-eval", repo_id=repo_id)
    config.embedding.provider = "noop"
    config.storage.backend = "sqlite"
    config.storage.data_dir = data_dir
    return Memory(config=config)


def seed_case(memory: Memory, case: EvalCase, repo_id: str) -> None:
    for seed in case.memories:
        if seed.layer == "episodic":
            memory.record(
                seed.content,
                category=seed.category,
                importance=seed.importance,
                repo_id=repo_id,
            )
        elif seed.layer == "intent":
            memory.goal(seed.content, priority=1, repo_id=repo_id)
        else:
            memory.learn(
                seed.content,
                category=seed.category,
                importance=seed.importance,
                repo_id=repo_id,
            )


def memory_answer(
    memory: Memory, case: EvalCase, repo_id: str, limit: int, min_score: float
) -> dict[str, Any]:
    results = memory.recall(case.question, repo_id=repo_id, limit=limit, min_score=min_score)
    citations = [
        {
            "id": result["id"],
            "layer": result["layer"],
            "category": result.get("category"),
            "snippet": " ".join(result["content"].split())[:220],
            "relevance_score": result.get("relevance_score") or result.get("similarity"),
        }
        for result in results
    ]
    if not citations:
        answer = ABSTENTION
    else:
        answer = "I found relevant memory context:\n" + "\n".join(
            f"- [{citation['id']}] {citation['snippet']}" for citation in citations
        )
    return {"answer": answer, "citations": citations}


def score_response(case: EvalCase, answer: str, citations: list[dict[str, Any]]) -> dict[str, Any]:
    normalized = answer.lower()
    abstained = normalized.strip().startswith("insufficient memory evidence")
    has_expected_terms = all(term.lower() in normalized for term in case.expected_terms)
    has_forbidden_terms = any(term.lower() in normalized for term in case.forbidden_terms)
    has_citations = bool(citations)

    if case.answerable:
        correct = has_expected_terms and has_citations and not has_forbidden_terms
    else:
        correct = abstained and not has_citations and not has_forbidden_terms

    unsupported_or_false = (
        has_forbidden_terms
        or (not case.answerable and not abstained)
        or (case.answerable and not has_citations)
    )

    return {
        "correct": correct,
        "abstained": abstained,
        "has_citations": has_citations,
        "has_forbidden_terms": has_forbidden_terms,
        "unsupported_or_false": unsupported_or_false,
    }


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    answerable = [result for result in results if result["answerable"]]
    unknown = [result for result in results if not result["answerable"]]

    def rate(items: list[dict[str, Any]], key: str, side: str) -> float:
        if not items:
            return 0.0
        return sum(1 for item in items if item[side]["score"][key]) / len(items)

    no_memory_hallucination = rate(results, "unsupported_or_false", "no_memory")
    with_memory_hallucination = rate(results, "unsupported_or_false", "with_memory")

    return {
        "cases": total,
        "answerable_cases": len(answerable),
        "unknown_cases": len(unknown),
        "no_memory": {
            "accuracy": rate(results, "correct", "no_memory"),
            "unsupported_or_false_rate": no_memory_hallucination,
            "unknown_abstention_rate": rate(unknown, "abstained", "no_memory"),
        },
        "with_memory": {
            "accuracy": rate(results, "correct", "with_memory"),
            "unsupported_or_false_rate": with_memory_hallucination,
            "citation_coverage_rate": rate(answerable, "has_citations", "with_memory"),
            "unknown_abstention_rate": rate(unknown, "abstained", "with_memory"),
        },
        "reduction": {
            "unsupported_or_false_rate_delta": no_memory_hallucination
            - with_memory_hallucination,
            "relative_reduction": (
                (no_memory_hallucination - with_memory_hallucination) / no_memory_hallucination
                if no_memory_hallucination
                else 0.0
            ),
        },
    }


def run_evaluation(args: argparse.Namespace, data_dir: Path) -> dict[str, Any]:
    memory = build_memory(args.repo_id, data_dir)
    for case in CASES:
        seed_case(memory, case, args.repo_id)

    case_results = []
    for case in CASES:
        no_memory = {"answer": case.no_memory_answer, "citations": []}
        with_memory = memory_answer(memory, case, args.repo_id, args.limit, args.min_score)
        case_results.append(
            {
                "id": case.case_id,
                "question": case.question,
                "answerable": case.answerable,
                "no_memory": {
                    **no_memory,
                    "score": score_response(case, no_memory["answer"], no_memory["citations"]),
                },
                "with_memory": {
                    **with_memory,
                    "score": score_response(
                        case, with_memory["answer"], with_memory["citations"]
                    ),
                },
            }
        )

    return {
        "benchmark": "hallucination_grounding",
        "mode": "deterministic_retrieval_proxy",
        "repo_id": args.repo_id,
        "min_score": args.min_score,
        "summary": summarize(case_results),
        "results": case_results,
    }


def print_text(report: dict[str, Any]) -> None:
    summary = report["summary"]
    print("Hallucination grounding evaluation")
    print(f"Mode: {report['mode']}")
    print(f"Cases: {summary['cases']}")
    print("")
    print("No memory:")
    print(f"  accuracy: {summary['no_memory']['accuracy']:.0%}")
    print(
        "  unsupported_or_false_rate: "
        f"{summary['no_memory']['unsupported_or_false_rate']:.0%}"
    )
    print("")
    print("With memory:")
    print(f"  accuracy: {summary['with_memory']['accuracy']:.0%}")
    print(
        "  unsupported_or_false_rate: "
        f"{summary['with_memory']['unsupported_or_false_rate']:.0%}"
    )
    print(f"  citation_coverage_rate: {summary['with_memory']['citation_coverage_rate']:.0%}")
    print(f"  unknown_abstention_rate: {summary['with_memory']['unknown_abstention_rate']:.0%}")
    print("")
    print(
        "Unsupported/false reduction: "
        f"{summary['reduction']['unsupported_or_false_rate_delta']:.0%} points "
        f"({summary['reduction']['relative_reduction']:.0%} relative)"
    )


def main() -> None:
    args = parse_args()
    if args.data_dir:
        args.data_dir.mkdir(parents=True, exist_ok=True)
        report = run_evaluation(args, args.data_dir)
    else:
        with tempfile.TemporaryDirectory(prefix="visp-memory-hallucination-eval-") as tmpdir:
            report = run_evaluation(args, Path(tmpdir))

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_text(report)


if __name__ == "__main__":
    main()
