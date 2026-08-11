"""Token-budgeted, provenance-aware context compilation."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from typing import Any, Optional

from visp_memory.core.clock import parse_utc, utc_now
from visp_memory.core.eligibility import (
    EligibilityFilterResult,
    EligibilityRejection,
    assess_recall_eligibility,
    normalize_optional_scope_values,
    require_repo_id,
)
from visp_memory.core.hybrid_retrieval import HybridRetriever
from visp_memory.core.tokens import estimate_tokens


class ContextCompiler:
    """Build compact context from text, graph, temporal, and code-entity signals.

    ``code_graph`` is intel's file-grain projection, or ``None``. It is optional at
    every call site on purpose: a caller that has no checkout to point at compiles
    exactly the context it compiled before structural conditioning existed.
    """

    def __init__(self, storage, code_graph=None):
        self.storage = storage
        self.code_graph = code_graph

    @staticmethod
    def _terms(content: str) -> set[str]:
        return set(re.findall(r"[a-z0-9_./-]+", content.casefold()))

    def compile(
        self,
        query: str,
        *,
        repo_id: Optional[str],
        token_budget: int = 2000,
        as_of=None,
        files: Optional[list[str]] = None,
        symbols: Optional[list[str]] = None,
        previous_fingerprint: Optional[str] = None,
        min_confidence: float = 0.0,
        memory_filter: Optional[Callable[[dict[str, Any]], bool]] = None,
        environment: Any = None,
        task_type: Any = None,
    ) -> dict[str, Any]:
        repo_id = require_repo_id(repo_id)
        environment = list(
            normalize_optional_scope_values(environment, field="environment")
        ) or None
        task_type = list(
            normalize_optional_scope_values(task_type, field="task_type")
        ) or None
        as_of_time = parse_utc(as_of) or utc_now()
        if as_of is not None and parse_utc(as_of) is None:
            raise ValueError("as_of must be a valid timestamp")
        files = files or []
        symbols = symbols or []
        eligibility_rejections: dict[str, EligibilityRejection] = {}
        eligibility_allowed: dict[str, dict[str, Any]] = {}

        def candidate_filter(memory: dict[str, Any]) -> bool:
            if memory_filter and not memory_filter(memory):
                return False
            eligibility = assess_recall_eligibility(
                memory,
                repo_id=repo_id,
                environment=environment,
                task_type=task_type,
                as_of=as_of_time,
            )
            if not eligibility.eligible:
                eligibility_rejections[str(memory.get("id"))] = EligibilityRejection(
                    memory, eligibility
                )
                return False
            eligibility_allowed[str(memory.get("id"))] = memory
            metadata = memory.get("metadata") or {}
            confidence = float(metadata.get("confidence", 0.5) or 0.0)
            return confidence >= min_confidence

        ranked = HybridRetriever(self.storage, code_graph=self.code_graph).retrieve(
            query,
            repo_id=repo_id,
            files=files,
            symbols=symbols,
            limit=80,
            candidate_filter=candidate_filter,
            environment=environment,
            task_type=task_type,
            as_of=as_of_time,
        )
        selected: list[dict[str, Any]] = []
        selected_terms: list[set[str]] = []
        consumed_tokens = 0
        for memory in ranked:
            content_terms = self._terms(memory.get("content", ""))
            maximum_overlap = max(
                (
                    len(content_terms & prior) / max(len(content_terms | prior), 1)
                    for prior in selected_terms
                ),
                default=0.0,
            )
            if maximum_overlap > 0.85:
                continue
            token_cost = estimate_tokens(memory.get("content", "")) + 18
            if consumed_tokens + token_cost > max(64, token_budget):
                continue
            metadata = memory.get("metadata") or {}
            selected.append(
                {
                    "id": memory["id"],
                    "content": memory.get("content", ""),
                    "layer": memory.get("layer"),
                    "category": memory.get("category"),
                    "repo_id": memory.get("repo_id"),
                    "tags": memory.get("tags") or [],
                    "source_ids": memory.get("source_ids") or [],
                    "relevance_score": memory.get("relevance_score")
                    or memory.get("similarity"),
                    "confidence": float(metadata.get("confidence", 0.5) or 0.0),
                    "observed_at": metadata.get("observed_at") or memory.get("created_at"),
                    "valid_from": metadata.get("valid_from"),
                    "valid_to": metadata.get("valid_to"),
                    "source_revision": metadata.get("source_revision"),
                    "source_hash": metadata.get("source_hash"),
                    "evidence": metadata.get("evidence") or [],
                    "files": metadata.get("files") or metadata.get("applies_to") or [],
                    "symbols": metadata.get("symbols") or [],
                    "retrieval_channels": memory.get("retrieval_channels") or [],
                    "retrieval_factors": memory.get("retrieval_factors") or {},
                    "ranking_explanation": memory.get("ranking_explanation") or [],
                    "token_cost": token_cost,
                }
            )
            selected_terms.append(content_terms)
            consumed_tokens += token_cost

        fingerprint_payload = {
            "environment": environment,
            "task_type": task_type,
            "items": [
                {
                    "id": item["id"],
                    "content": item["content"],
                    "valid_to": item["valid_to"],
                    "confidence": item["confidence"],
                }
                for item in selected
            ],
        }
        fingerprint = hashlib.sha256(
            json.dumps(fingerprint_payload, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()[:24]
        unchanged = bool(previous_fingerprint and previous_fingerprint == fingerprint)
        top_score = max(
            (float(item.get("relevance_score") or 0.0) for item in selected),
            default=0.0,
        )
        abstained = not selected or top_score < 0.08
        text = ""
        if not unchanged and not abstained:
            text = "\n\n".join(
                f"[{item['id']}] ({item['layer']}) {item['content']}" for item in selected
            )
        channel_counts: dict[str, int] = {}
        for memory in ranked:
            for channel in memory.get("retrieval_channels") or []:
                channel_counts[channel] = channel_counts.get(channel, 0) + 1
        return {
            "query": query,
            "repo_id": repo_id,
            "environment": environment,
            "task_type": task_type,
            "as_of": as_of_time.isoformat(),
            "token_budget": token_budget,
            "token_count": consumed_tokens if not unchanged else 0,
            "fingerprint": fingerprint,
            "unchanged": unchanged,
            "abstained": abstained,
            "abstention_reason": "Insufficient relevant evidence" if abstained else None,
            "items": [] if unchanged else selected,
            "context": text,
            "retrieval": {
                "strategy": "hybrid_rrf_ppr",
                "candidate_count": len(ranked),
                "selected_count": len(selected),
                "channel_counts": channel_counts,
            },
            "eligibility_filter": EligibilityFilterResult(
                allowed=list(eligibility_allowed.values()),
                rejected=list(eligibility_rejections.values()),
                considered_count=len(eligibility_allowed) + len(eligibility_rejections),
            ).diagnostics(),
        }
