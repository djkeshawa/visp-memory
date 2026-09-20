"""Shared memory relevance scoring utilities."""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Any

from visp_memory.core.numeric import bounded_float

DEFAULT_RECALL_MIN_SCORE = 0.56
UTILITY_RANKING_LIMIT = 0.08
CONTEXT_RANKING_LIMIT = 0.12
# Ceiling on the *combined* positive boost from all secondary signals (utility +
# context + activation). Individually each is small, but their sum (up to 0.26) could
# otherwise let a weakly-relevant-but-popular memory edge out a strong direct match.
# Capping the total keeps the core invariant intact: secondary signals tune ties, they
# never override direct query relevance. Negative utility (dismissals) is not capped.
TOTAL_ADJUSTMENT_LIMIT = 0.15
# Retrieval-induced strengthening ("use it or lose it"): memories that are actually
# retrieved accumulate access count, and frequently-retrieved memories should be a
# little easier to recall next time. This mirrors ACT-R base-level activation and the
# recency/frequency model used by human-memory agents. The contribution is small and
# strictly bounded so it tunes ties without ever overriding direct query relevance.
ACTIVATION_RANKING_LIMIT = 0.06
ACTIVATION_SATURATION = 20.0
# Spaced-repetition decay (MemoryBank's R = e^(-t/S), S grows with each recall): repeated
# use flattens a memory's forgetting curve. We model strength S as a function of access
# count and stretch the half-life accordingly, so a frequently-used memory fades far more
# slowly than a one-off note. access_count == 0 leaves the base half-life unchanged, so
# never-used memories decay exactly as before (backward compatible).
DECAY_STRENGTH_FACTOR = 1.0
CONTEXT_FACTOR_WEIGHTS = {
    "session": 0.03,
    "task": 0.04,
    "file": 0.05,
    "repo": 0.02,
    "dependency": 0.03,
    "constraint": 0.03,
    "active_intent": 0.05,
}
_LEXICAL_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "for",
    "had",
    "has",
    "how",
    "in",
    "is",
    "it",
    "no",
    "not",
    "of",
    "on",
    "or",
    "the",
    "this",
    "to",
    "was",
    "what",
    "when",
    "where",
    "why",
    "with",
}


def clamp_score(value: Any, default: float = 0.0) -> float:
    """Normalize arbitrary numeric input to the 0..1 scoring range."""
    normalized = bounded_float(value, default=default)
    return default if normalized is None else normalized


def utility_rank_adjustment(value: Any) -> float:
    """Return a bounded utility contribution for ranking."""
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    score = max(-1.0, min(1.0, score))
    return score * UTILITY_RANKING_LIMIT


def context_rank_adjustment(factors: Any) -> float:
    """Return a bounded ranking contribution from contextual recall factors."""
    if not isinstance(factors, dict):
        return 0.0

    adjustment = 0.0
    for factor, details in factors.items():
        weight = CONTEXT_FACTOR_WEIGHTS.get(factor, 0.0)
        if isinstance(details, dict):
            score = details.get("score", 0.0)
        else:
            score = details
        adjustment += weight * clamp_score(score)
    return min(CONTEXT_RANKING_LIMIT, adjustment)


def activation_rank_adjustment(access_count: Any) -> float:
    """Return a bounded recall boost from how often a memory has been retrieved.

    Uses a saturating logarithm so the first few retrievals matter most and the
    contribution levels off, preventing a runaway popularity bias. Always
    non-negative and capped at ``ACTIVATION_RANKING_LIMIT``.
    """
    try:
        count = float(access_count)
    except (TypeError, ValueError):
        return 0.0
    if count <= 0:
        return 0.0
    normalized = math.log1p(count) / math.log1p(ACTIVATION_SATURATION)
    return ACTIVATION_RANKING_LIMIT * min(1.0, normalized)


def effective_halflife_days(halflife_days: Any, access_count: Any = 0) -> float:
    """Return a decay half-life stretched by how often a memory has been recalled.

    Models memory strength growing with use (spaced repetition): the more a memory is
    retrieved, the slower it forgets. Never falls below the base half-life.
    """
    try:
        base = max(1.0, float(halflife_days))
    except (TypeError, ValueError):
        base = 1.0
    try:
        count = max(0.0, float(access_count))
    except (TypeError, ValueError):
        count = 0.0
    return base * (1.0 + DECAY_STRENGTH_FACTOR * math.log1p(count))


def projected_importance(
    importance: Any,
    age_days: Any,
    halflife_days: Any,
    access_count: Any = 0,
    min_importance: float = 0.0,
) -> float:
    """Project a memory's importance after exponential, use-aware decay.

    Combines the two spaced-repetition effects: idle time (``age_days``) drives
    exponential forgetting, while ``access_count`` stretches the half-life so used
    memories persist. Floored at ``min_importance``.
    """
    try:
        current = float(importance)
    except (TypeError, ValueError):
        current = 0.5
    try:
        age = max(0.0, float(age_days))
    except (TypeError, ValueError):
        age = 0.0
    half = effective_halflife_days(halflife_days, access_count)
    factor = 0.5 ** (age / half)
    return max(float(min_importance), current * factor)


def normalize_distance_score(distance: Any) -> float:
    """Convert a vector distance where lower is better into a bounded similarity."""
    try:
        value = float(distance)
    except (TypeError, ValueError):
        return 0.0
    return clamp_score(1.0 - value)


def text_similarity(query: str, content: str) -> float:
    """Return a simple lexical overlap score for deterministic fallback ranking."""
    query_terms = {
        term.lower()
        for term in re.findall(r"[a-zA-Z0-9_]+", query)
        if term.lower() not in _LEXICAL_STOPWORDS
    }
    content_terms = {
        term.lower()
        for term in re.findall(r"[a-zA-Z0-9_]+", content)
        if term.lower() not in _LEXICAL_STOPWORDS
    }
    if not query_terms:
        return 0.0
    return len(query_terms & content_terms) / len(query_terms)


# A recall query lists candidate handles; scoring divides matches over at most
# this many of them. Without the cap, "add an overdue marker that compares due
# dates in todos.json" scored LOWER than "due dates" against the same memory —
# every filler word diluted the overlap, so the goal-shaped queries the
# coordinator's memory fusion sends could never clear the recall threshold.
# Queries of this length or shorter score exactly as before.
_RECALL_QUERY_TERM_CAP = 4


def _recall_overlap(query: str, content: str) -> float:
    """Dilution-resistant lexical overlap, for recall ranking ONLY.

    relationship_score deliberately keeps the strict full-ratio
    text_similarity: graph links are conservative by design, and this cap
    must never loosen them.
    """
    query_terms = {
        term.lower()
        for term in re.findall(r"[a-zA-Z0-9_]+", query)
        if term.lower() not in _LEXICAL_STOPWORDS
    }
    content_terms = {
        term.lower()
        for term in re.findall(r"[a-zA-Z0-9_]+", content)
        if term.lower() not in _LEXICAL_STOPWORDS
    }
    if not query_terms:
        return 0.0
    matched_terms = query_terms & content_terms
    matched = len(matched_terms)
    # The cap engages only for overlaps that could not be coincidence, with
    # thresholds learned from this project's own quality floors:
    # - fewer than three shared terms stays strict — the task-brief benchmark
    #   requires ABSTAINING on a two-term topical graze ("payment settlement"
    #   against one payments memory), and paths fragment into junk that
    #   cross-matches ({src, py} from two unrelated file mentions);
    # - at least one shared term must be substantive (five or more characters
    #   or carrying a digit), never debris alone.
    # A genuine goal-to-memory hit shares the nouns and the file names — three
    # or more terms in practice — and only that shape earns the cap.
    specific = any(len(term) >= 5 or any(ch.isdigit() for ch in term) for term in matched_terms)
    if matched < 3 or not specific:
        return matched / len(query_terms)
    return min(1.0, matched / min(len(query_terms), _RECALL_QUERY_TERM_CAP))


def relationship_score(similarity: Any, query: str, content: str) -> float:
    """
    Score whether two memories should be connected in the graph.

    Embedding models can produce a non-trivial similarity floor for unrelated short
    project notes. Requiring lexical support keeps inferred graph links conservative.
    """
    semantic = clamp_score(similarity)
    lexical = text_similarity(query, content)
    if lexical == 0.0 and semantic < 0.78:
        return 0.0
    return clamp_score((semantic * 0.75) + (lexical * 0.25))


def graph_edge_score(strength: Any, evidence: dict[str, Any] | None = None) -> float:
    """Score a graph edge using relationship strength and evidence confidence."""
    evidence = evidence or {}
    strength_score = clamp_score(strength, default=0.5)
    confidence_score = clamp_score(evidence.get("confidence_score"), default=strength_score)
    confidence = evidence.get("confidence")
    confidence_bonus = {
        "observed": 0.10,
        "manual": 0.08,
        "inferred": 0.04,
        "ambiguous": 0.0,
    }.get(confidence, 0.0)
    return clamp_score((strength_score * 0.45) + (confidence_score * 0.45) + confidence_bonus)


def graph_node_relevance(
    query_score: Any,
    importance: Any,
    edge_score: Any,
    distance: Any,
) -> float:
    """Score graph recall nodes while penalizing distant context."""
    try:
        distance_value = max(0, int(distance))
    except (TypeError, ValueError):
        distance_value = 0
    distance_score = 1.0 / (1.0 + distance_value)
    return clamp_score(
        (clamp_score(query_score) * 0.45)
        + (clamp_score(importance, default=0.5) * 0.20)
        + (clamp_score(edge_score, default=0.5) * 0.25)
        + (distance_score * 0.10)
    )


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
    lexical = _recall_overlap(query or "", str(memory.get("content", ""))) if query else 0.0
    importance = clamp_score(memory.get("importance"), default=0.5)
    recency = _age_score(memory)

    if query:
        score = similarity * 0.50 + lexical * 0.30 + importance * 0.15 + recency * 0.05
        if memory.get("retrieval_method") == "semantic":
            # A vector match may express the same idea without sharing words.
            # Keep lexical boosts, but do not halve a real semantic match merely
            # because a paraphrase has no token overlap. Keyword behavior is unchanged.
            score = max(score, similarity)
    else:
        score = importance * 0.70 + recency * 0.30

    adjustment = (
        utility_rank_adjustment(memory.get("utility_score"))
        + context_rank_adjustment(memory.get("ranking_factors"))
        + activation_rank_adjustment(memory.get("access_count"))
    )
    # Cap the combined *positive* boost; leave penalties (negative utility) intact.
    adjustment = min(adjustment, TOTAL_ADJUSTMENT_LIMIT)
    return clamp_score(score + adjustment)


def explain_ranking_factors(memory: dict[str, Any]) -> list[str]:
    """Format contextual ranking factors for API/MCP clients."""
    factors = memory.get("ranking_factors")
    if not isinstance(factors, dict):
        return []

    explanations = []
    for name, details in sorted(factors.items()):
        if isinstance(details, dict):
            score = clamp_score(details.get("score", 0.0))
            reason = details.get("reason") or "matched recall context"
        else:
            score = clamp_score(details)
            reason = "matched recall context"
        adjustment = CONTEXT_FACTOR_WEIGHTS.get(name, 0.0) * score
        explanations.append(f"{name}: +{adjustment:.3f} ({reason})")
    return explanations


def rank_memory_results(
    memories: list[dict[str, Any]],
    query: str | None = None,
    limit: int | None = None,
    dedupe: bool = True,
    min_score: float | None = None,
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
        explanation = explain_ranking_factors(scored)
        if explanation:
            scored["ranking_explanation"] = explanation
        if min_score is not None and scored["relevance_score"] < clamp_score(min_score):
            continue
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
