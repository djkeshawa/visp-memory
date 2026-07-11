#!/usr/bin/env python3
"""Evaluate Task Memory Brief accuracy, safety, and token efficiency."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from llm_memory.core.storage import LocalStorage
from llm_memory.core.task_brief import TaskMemoryBriefCompiler
from llm_memory.core.tokens import estimate_tokens


def evaluate() -> dict:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
        storage = LocalStorage(Path(directory))
        intent_id = storage.set_intent(
            "Harden dashboard authentication",
            priority=3,
            repo_id="evaluation",
            context={
                "constraints": ["Preserve scoped API access"],
                "acceptance_criteria": ["Browser credentials never reach local storage"],
            },
        )
        warning = storage.store_memory(
            "WARNING [auth]: browser credentials must stay in HttpOnly cookies",
            layer="semantic",
            category="fragile_area",
            repo_id="evaluation",
            metadata={"files": ["src/auth.py"], "confidence": 0.98},
            auto_link=False,
        )
        decision = storage.store_memory(
            "Use opaque server-side sessions with CSRF validation",
            layer="episodic",
            category="architecture_decision",
            repo_id="evaluation",
            metadata={"files": ["src/auth.py"], "confidence": 0.96},
            auto_link=False,
        )
        knowledge = storage.store_memory(
            "Machine clients use repository-scoped llmm personal access tokens",
            layer="semantic",
            category="fact",
            repo_id="evaluation",
            metadata={"files": ["src/auth.py"], "confidence": 0.94},
            auto_link=False,
        )
        old = storage.store_memory(
            "Dashboard credentials are persisted in local storage",
            layer="semantic",
            category="fact",
            repo_id="evaluation",
            status="superseded",
            metadata={"files": ["src/auth.py"], "confidence": 0.8},
            auto_link=False,
        )
        storage.add_relationship(
            warning,
            old,
            "contradicts",
            evidence={
                "confidence": "observed",
                "confidence_score": 0.98,
                "reason": "The browser session design removed credential persistence.",
            },
        )
        for index in range(30):
            storage.store_memory(
                f"Unrelated build pipeline observation {index}",
                layer="episodic",
                repo_id="evaluation",
                metadata={"files": ["src/build.py"], "confidence": 0.8},
                auto_link=False,
            )

        all_memories = storage.list_memories(
            repo_id="evaluation", status="all", limit=1000
        )
        full_tokens = sum(estimate_tokens(memory["content"]) for memory in all_memories)
        brief = TaskMemoryBriefCompiler(storage).prepare(
            "Implement secure login behavior in `src/auth.py`",
            repo_id="evaluation",
            intent_id=intent_id,
            token_budget=400,
        )
        relevant_ids = {warning, decision, knowledge}
        returned_ids = {citation["memory_id"] for citation in brief["citations"]}
        precision = len(returned_ids.intersection(relevant_ids)) / max(len(returned_ids), 1)
        recall = len(returned_ids.intersection(relevant_ids)) / len(relevant_ids)
        savings_ratio = 1.0 - (brief["token_count"] / max(full_tokens, 1))
        abstention = TaskMemoryBriefCompiler(storage).prepare(
            "Change payment settlement behavior",
            repo_id="evaluation",
            files=["src/payments.py"],
            token_budget=250,
        )
        passed = (
            precision >= 0.95
            and recall >= 0.95
            and savings_ratio >= 0.2
            and len(brief["contradictions"]) == 1
            and abstention["abstained"]
        )
        return {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "full_store_tokens": full_tokens,
            "brief_tokens": brief["token_count"],
            "token_savings_ratio": round(savings_ratio, 4),
            "contradictions_recovered": len(brief["contradictions"]),
            "weak_evidence_abstained": abstention["abstained"],
            "passed": passed,
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    arguments = parser.parse_args()
    result = evaluate()
    print(json.dumps(result, indent=2) if arguments.json else result)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
