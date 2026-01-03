"""Token-budgeted, provenance-aware context compilation."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from typing import Any, Optional

from visp_memory.core.clock import parse_utc, utc_now
from visp_memory.core.hybrid_retrieval import HybridRetriever
from visp_memory.core.tokens import estimate_tokens


class ContextCompiler:
    """Build compact context from text, graph, temporal, and code-entity signals."""

    def __init__(self, storage):
        self.storage = storage

    @staticmethod
    def _terms(content: str) -> set[str]:
        return set(re.findall(r"[a-z0-9_./-]+", content.casefold()))

    @staticmethod
    def _is_current(memory: dict[str, Any], as_of) -> bool:
        metadata = memory.get("metadata") or {}
        valid_from = parse_utc(metadata.get("valid_from"))
        valid_to = parse_utc(metadata.get("valid_to"))
        return not ((valid_from and valid_from > as_of) or (valid_to and valid_to <= as_of))

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
    ) -> dict[str, Any]:
        as_of_time = parse_utc(as_of) or utc_now()
        files = files or []
        symbols = symbols or []
        def candidate_filter(memory: dict[str, Any]) -> bool:
            if memory_filter and not memory_filter(memory):
                return False
            metadata = memory.get("metadata") or {}
            confidence = float(metadata.get("confidence", 0.5) or 0.0)
            return confidence >= min_confidence and self._is_current(memory, as_of_time)

        ranked = HybridRetriever(self.storage).retrieve(
            query,
            repo_id=repo_id,
            files=files,
            symbols=symbols,
            limit=80,
            candidate_filter=candidate_filter,
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

        fingerprint_payload = [
            {
                "id": item["id"],
                "content": item["content"],
                "valid_to": item["valid_to"],
                "confidence": item["confidence"],
            }
            for item in selected
        ]
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
        }
