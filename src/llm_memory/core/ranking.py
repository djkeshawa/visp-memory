"""Shared memory relevance scoring utilities."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def clamp_score(value: Any, default: float = 0.0) -> float:
    """Normalize arbitrary numeric input to the 0..1 scoring range."""
    try:
        score = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, score))


def normalize_distance_score(distance: Any) -> float:
    """Convert a vector distance where lower is better into a bounded similarity."""
    try:
        value = float(distance)
    except (TypeError, ValueError):
        return 0.0
    return clamp_score(1.0 - value)


def text_similarity(query: str, content: str) -> float:
    """Return a simple lexical overlap score for deterministic fallback ranking."""
    query_terms = {term.lower() for term in query.split() if term.strip()}
    content_terms = {term.lower() for term in content.split() if term.strip()}
    if not query_terms:
        return 0.0
    return len(query_terms & content_terms) / len(query_terms)


def _age_score(memory: dict[str, Any]) -> float:
    """Score recent memories higher without making recency dominate relevance."""
    raw_timestamp = memory.get("accessed_at") or memory.get("created_at")
    if not raw_timestamp:
        return 0.0

    if isinstance(raw_timestamp, datetime):
        timestamp = raw_timestamp
    else:
        try:
            timestamp = datetime.fromisoformat(str(raw_timestamp).replace("Z", "+00:00"))
        except ValueError:
            return 0.0

    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)

    age_days = max(0.0, (datetime.now(timezone.utc) - timestamp).total_seconds() / 86400)
    return 1.0 / (1.0 + age_days / 30.0)


def score_memory_result(memory: dict[str, Any], query: str | None = None) -> float:
    """
    Calculate one canonical relevance score for memory search results.

    Vector similarity can be uninformative when the noop embedding provider is active, so
    lexical overlap and importance are included in every score. Recency is deliberately a
    small tie-breaker rather than a primary signal.
    """
    similarity = clamp_score(memory.get("similarity"), default=0.0)
    lexical = text_similarity(query or "", str(memory.get("content", ""))) if query else 0.0
    importance = clamp_score(memory.get("importance"), default=0.5)
    recency = _age_score(memory)

    if query:
        score = similarity * 0.50 + lexical * 0.30 + importance * 0.15 + recency * 0.05
    else:
        score = importance * 0.70 + recency * 0.30

    return clamp_score(score)


def rank_memory_results(
    memories: list[dict[str, Any]],
    query: str | None = None,
    limit: int | None = None,
    dedupe: bool = True,
) -> list[dict[str, Any]]:
    """Attach relevance_score and return memories in canonical relevance order."""
    ranked: list[dict[str, Any]] = []
    seen: set[str] = set()

    for memory in memories:
        memory_id = memory.get("id")
        if dedupe and memory_id:
            if memory_id in seen:
                continue
            seen.add(memory_id)

        scored = dict(memory)
        scored["relevance_score"] = score_memory_result(scored, query=query)
        ranked.append(scored)

    ranked.sort(
        key=lambda item: (
            item.get("relevance_score", 0.0),
            item.get("similarity", 0.0),
            item.get("importance", 0.0),
            str(item.get("created_at", "")),
        ),
        reverse=True,
    )
    return ranked[:limit] if limit is not None else ranked
