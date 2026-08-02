"""Deterministic context-compiler accuracy and token-efficiency evaluation."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from visp_memory.core.context_compiler import ContextCompiler
from visp_memory.core.storage import LocalStorage
from visp_memory.core.tokens import estimate_tokens


def _store(storage, content, **kwargs):
    if kwargs.get("layer") == "semantic":
        kwargs["evidence_ids"] = [
            storage.store_evidence(content, repo_id=kwargs["repo_id"])
        ]
    return storage.store_memory(content, **kwargs)


def evaluate() -> dict:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
        storage = LocalStorage(Path(directory))
        relevant_ids = {
            _store(storage,
                "Dashboard login uses HttpOnly session cookies and CSRF validation",
                layer="semantic",
                repo_id="evaluation",
                metadata={"files": ["src/auth.py"], "confidence": 0.95},
                auto_link=False,
            ),
            _store(storage,
                "API clients authenticate with scoped llmm personal access tokens",
                layer="semantic",
                repo_id="evaluation",
                metadata={"files": ["src/auth.py"], "confidence": 0.9},
                auto_link=False,
            ),
        }
        _store(storage,
            "The dashboard previously stored JWT tokens in browser session storage",
            layer="semantic",
            repo_id="evaluation",
            metadata={
                "files": ["src/auth.py"],
                "confidence": 0.8,
                "valid_to": "2024-01-01T00:00:00+00:00",
            },
            auto_link=False,
        )
        for index in range(20):
            _store(storage,
                f"Unrelated build observation {index}",
                repo_id="evaluation",
                metadata={"files": ["src/build.py"], "confidence": 0.7},
                auto_link=False,
            )

        all_memories = storage.list_memories(repo_id="evaluation", status="active", limit=1000)
        full_tokens = sum(estimate_tokens(memory["content"]) for memory in all_memories)
        compiled = ContextCompiler(storage).compile(
            "How does dashboard and API authentication work?",
            repo_id="evaluation",
            files=["src/auth.py"],
            as_of="2026-01-01T00:00:00+00:00",
            token_budget=300,
        )
        returned_ids = {item["id"] for item in compiled["items"]}
        precision = len(returned_ids & relevant_ids) / max(len(returned_ids), 1)
        recall = len(returned_ids & relevant_ids) / len(relevant_ids)
        savings_ratio = 1.0 - (compiled["token_count"] / max(full_tokens, 1))
        return {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "full_store_tokens": full_tokens,
            "context_tokens": compiled["token_count"],
            "token_savings_ratio": round(savings_ratio, 4),
            "passed": precision >= 0.95 and recall >= 0.95 and savings_ratio >= 0.2,
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
