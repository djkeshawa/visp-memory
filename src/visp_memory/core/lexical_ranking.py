"""Bounded BM25 reranking of already eligible recall candidates."""

import math
import re
from collections import Counter

HYBRID_CANDIDATE_LIMIT = 100
RRF_K = 60
RANKING_STRATEGIES = ("default", "hybrid", "hybrid_union")


def validate_ranking_strategy(strategy: str) -> None:
    """Use the same explicit ranking choices across recall and native context."""
    if strategy not in RANKING_STRATEGIES:
        choices = "', '".join(RANKING_STRATEGIES)
        raise ValueError(f"ranking_strategy must be one of '{choices}'")


def bm25_scores(memories: list[dict], query: str) -> list[float]:
    """Score within the candidate pool; this is not a full-corpus lexical index."""
    if not memories:
        return []
    terms = [Counter(re.findall(r"\w+", str(row.get("content") or "").lower())) for row in memories]
    lengths = [sum(counts.values()) for counts in terms]
    average = sum(lengths) / len(lengths) or 1
    frequencies = Counter(term for counts in terms for term in counts)
    query_terms = sorted(set(re.findall(r"\w+", query.lower())))
    scores = []
    for counts, length in zip(terms, lengths, strict=True):
        score = 0.0
        for term in query_terms:
            frequency = counts[term]
            inverse_frequency = math.log(
                1 + (len(memories) - frequencies[term] + 0.5) / (frequencies[term] + 0.5)
            )
            score += (
                inverse_frequency
                * frequency
                * 2.5
                / (frequency + 1.5 * (0.25 + 0.75 * length / average))
            )
        scores.append(score)
    return scores


def rerank_lexical(memories: list[dict], query: str) -> list[dict]:
    """Fuse original and lexical ranks without changing canonical relevance scores.

    Callers must apply authorization, eligibility and the relevance floor first.
    Work is linear in candidate text plus O(n log n) sorting; recall bounds n.
    """
    unique = {}
    for row in memories:
        unique.setdefault(str(row["id"]), row)
    rows = list(unique.values())
    lexical = bm25_scores(rows, query)
    lexical_order = sorted(range(len(rows)), key=lambda i: (-lexical[i], i))
    ranks = {i: rank for rank, i in enumerate(lexical_order, 1) if lexical[i] > 0}
    result = []
    for i, row in enumerate(rows):
        lexical_rank = ranks.get(i)
        fusion = 1 / (RRF_K + i + 1)
        if lexical_rank is not None:
            fusion += 1 / (RRF_K + lexical_rank)
        result.append(
            {
                **row,
                "hybrid_rank_score": fusion,
                "hybrid_ranks": {"original": i + 1, "lexical": lexical_rank},
            }
        )
    return sorted(
        result,
        key=lambda row: (
            -row["hybrid_rank_score"],
            row["hybrid_ranks"]["lexical"] or math.inf,
            row["hybrid_ranks"]["original"],
        ),
    )
