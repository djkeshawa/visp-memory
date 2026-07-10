"""Backend-portable hybrid retrieval with bounded associative graph recall."""

from __future__ import annotations

import math
import re
from collections import defaultdict
from collections.abc import Callable
from typing import Any, Optional

from llm_memory.core.ranking import clamp_score, graph_edge_score, rank_memory_results

PPR_DAMPING = 0.5
PPR_ITERATIONS = 20
PPR_TOLERANCE = 1e-8
RRF_K = 60
MAX_CORPUS_MEMORIES = 500
MAX_GRAPH_EDGES = 2000
MAX_SEEDS = 8
DIRECT_MIN_SCORE = 0.16
DIRECT_WITH_ENTITY_SCOPE_MIN_SCORE = 0.30

_STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "for",
    "from",
    "how",
    "in",
    "is",
    "it",
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
    "with",
}


def _terms(value: str) -> set[str]:
    return {
        term
        for term in re.findall(r"[a-z0-9_./-]+", value.casefold())
        if len(term) > 1 and term not in _STOP_WORDS
    }


def personalized_pagerank(
    seeds: dict[str, float],
    edges: list[dict[str, Any]],
    *,
    damping: float = PPR_DAMPING,
    iterations: int = PPR_ITERATIONS,
    tolerance: float = PPR_TOLERANCE,
) -> dict[str, float]:
    """Return deterministic, degree-normalized Personalized PageRank scores."""
    restart_total = sum(max(0.0, float(value)) for value in seeds.values())
    if restart_total <= 0:
        return {}
    restart = {
        node_id: max(0.0, float(value)) / restart_total
        for node_id, value in seeds.items()
        if value > 0
    }

    adjacency: dict[str, dict[str, float]] = defaultdict(dict)
    for edge in edges[:MAX_GRAPH_EDGES]:
        source = str(edge.get("source_id") or "")
        target = str(edge.get("target_id") or "")
        if not source or not target or source == target:
            continue
        evidence = edge.get("evidence") or {}
        weight = graph_edge_score(edge.get("strength"), evidence)
        if str(edge.get("relationship") or "") == "related_to":
            weight *= 0.6
        if weight <= 0:
            continue
        adjacency[source][target] = max(adjacency[source].get(target, 0.0), weight)
        adjacency[target][source] = max(adjacency[target].get(source, 0.0), weight)

    damping = max(0.0, min(1.0, float(damping)))
    ranks = dict(restart)
    nodes = sorted(set(restart).union(adjacency))
    for _ in range(max(1, int(iterations))):
        next_ranks = {node: (1.0 - damping) * restart.get(node, 0.0) for node in nodes}
        dangling_mass = 0.0
        for node in nodes:
            rank = ranks.get(node, 0.0)
            neighbors = adjacency.get(node) or {}
            weight_total = sum(neighbors.values())
            if weight_total <= 0:
                dangling_mass += rank
                continue
            for neighbor, weight in sorted(neighbors.items()):
                next_ranks[neighbor] = next_ranks.get(neighbor, 0.0) + (
                    damping * rank * weight / weight_total
                )
        if dangling_mass:
            for node, restart_weight in restart.items():
                next_ranks[node] = next_ranks.get(node, 0.0) + (
                    damping * dangling_mass * restart_weight
                )
        delta = sum(abs(next_ranks.get(node, 0.0) - ranks.get(node, 0.0)) for node in nodes)
        ranks = next_ranks
        if delta <= tolerance:
            break
    return {node: ranks.get(node, 0.0) for node in nodes if ranks.get(node, 0.0) > 0}


class HybridRetriever:
    """Fuse direct, code-entity, and associative graph rankings."""

    def __init__(self, storage):
        self.storage = storage

    @staticmethod
    def _matches_entities(
        memory: dict[str, Any], *, files: list[str], symbols: list[str]
    ) -> bool:
        metadata = memory.get("metadata") or {}
        memory_files = set(metadata.get("files") or metadata.get("applies_to") or [])
        memory_symbols = set(metadata.get("symbols") or [])
        return bool(
            (files and memory_files.intersection(files))
            or (symbols and memory_symbols.intersection(symbols))
        )

    @staticmethod
    def _seed_specificity(
        memory: dict[str, Any], query_terms: set[str], corpus_terms: list[set[str]]
    ) -> float:
        matched = query_terms.intersection(_terms(str(memory.get("content") or "")))
        if not matched or not corpus_terms:
            return 0.5
        document_count = len(corpus_terms)
        idf_values = []
        for term in matched:
            frequency = sum(1 for terms in corpus_terms if term in terms)
            idf_values.append(math.log((document_count + 1) / (frequency + 1)) + 1.0)
        maximum_idf = math.log(document_count + 1) + 1.0
        return clamp_score(sum(idf_values) / len(idf_values) / maximum_idf, default=0.5)

    @staticmethod
    def _rank_map(items: list[dict[str, Any]]) -> dict[str, int]:
        return {
            str(item["id"]): index
            for index, item in enumerate(items, start=1)
            if item.get("id")
        }

    @staticmethod
    def _rrf_score(
        memory_id: str,
        *,
        direct_ranks: dict[str, int],
        entity_ranks: dict[str, int],
        graph_ranks: dict[str, int],
    ) -> float:
        channels = (
            (direct_ranks, 1.0),
            (entity_ranks, 0.85),
            (graph_ranks, 0.75),
        )
        score = sum(
            weight / (RRF_K + ranks[memory_id])
            for ranks, weight in channels
            if memory_id in ranks
        )
        maximum = sum(weight / (RRF_K + 1) for _, weight in channels)
        return clamp_score(score / maximum)

    def retrieve(
        self,
        query: str,
        *,
        repo_id: Optional[str],
        files: Optional[list[str]] = None,
        symbols: Optional[list[str]] = None,
        limit: int = 80,
        candidate_filter: Optional[Callable[[dict[str, Any]], bool]] = None,
    ) -> list[dict[str, Any]]:
        """Return explainable hybrid results without relying on backend-specific queries."""
        files = files or []
        symbols = symbols or []
        candidates: dict[str, dict[str, Any]] = {}
        direct_candidates: dict[str, dict[str, Any]] = {}
        for layer in ("intent", "semantic", "episodic", "raw"):
            for memory in self.storage.search_memories(
                query=query,
                repo_id=repo_id,
                layer=layer,
                status="active",
                limit=40,
            ):
                if candidate_filter and not candidate_filter(memory):
                    continue
                memory_id = str(memory.get("id") or "")
                if not memory_id:
                    continue
                direct_candidates[memory_id] = memory

        corpus = []
        for memory in self.storage.list_memories(
            repo_id=repo_id,
            status="active",
            limit=MAX_CORPUS_MEMORIES,
        ):
            if candidate_filter and not candidate_filter(memory):
                continue
            corpus.append(memory)

        entity_candidates = [
            memory
            for memory in corpus
            if self._matches_entities(memory, files=files, symbols=symbols)
        ]
        for memory in entity_candidates:
            candidates[str(memory["id"])] = memory

        direct = rank_memory_results(list(direct_candidates.values()), query=query, limit=60)
        direct = [
            memory
            for memory in direct
            if float(memory.get("relevance_score") or 0.0) >= DIRECT_MIN_SCORE
            and (
                not (files or symbols)
                or self._matches_entities(memory, files=files, symbols=symbols)
                or float(memory.get("relevance_score") or 0.0)
                >= DIRECT_WITH_ENTITY_SCOPE_MIN_SCORE
            )
        ]
        for memory in direct:
            candidates[str(memory["id"])] = memory
        entity = rank_memory_results(entity_candidates, query=query, limit=60)
        corpus_terms = [_terms(str(memory.get("content") or "")) for memory in corpus]
        query_terms = _terms(query)
        seeds = {}
        seed_specificity = {}
        direct_seed_ids = {str(memory["id"]) for memory in direct}
        seed_candidates = list(direct[:5])
        selected_seed_ids = {str(memory["id"]) for memory in seed_candidates}
        seed_candidates.extend(
            memory for memory in entity if str(memory["id"]) not in selected_seed_ids
        )
        selected_seed_ids.update(str(memory["id"]) for memory in seed_candidates)
        seed_candidates.extend(
            memory for memory in direct[5:] if str(memory["id"]) not in selected_seed_ids
        )
        for memory in seed_candidates[:MAX_SEEDS]:
            memory_id = str(memory["id"])
            specificity = self._seed_specificity(memory, query_terms, corpus_terms)
            seed_specificity[memory_id] = specificity
            base_score = float(memory.get("relevance_score") or 0.0)
            if memory_id not in direct_seed_ids:
                base_score = max(0.52, base_score)
            seeds[memory_id] = max(0.01, base_score) * (0.5 + (0.5 * specificity))

        eligible_ids = {str(memory["id"]) for memory in corpus if memory.get("id")}
        eligible_ids.update(seeds)
        relationships = sorted(
            [
                relationship
                for relationship in self.storage.get_all_relationships(repo_id=repo_id)
                if str(relationship.get("source_id") or "") in eligible_ids
                and str(relationship.get("target_id") or "") in eligible_ids
            ],
            key=lambda item: (
                str(item.get("source_id") or ""),
                str(item.get("target_id") or ""),
                str(item.get("relationship") or ""),
                str(item.get("id") or ""),
            ),
        )[:MAX_GRAPH_EDGES]
        graph_scores = personalized_pagerank(seeds, relationships)
        graph_order = sorted(graph_scores, key=lambda item: (-graph_scores[item], item))
        graph_ranks = {memory_id: index for index, memory_id in enumerate(graph_order, start=1)}
        graph_memory_ids = set(graph_order[: max(limit * 2, 100)])
        for memory in corpus:
            memory_id = str(memory.get("id") or "")
            if memory_id in graph_memory_ids:
                candidates.setdefault(memory_id, memory)

        direct_ranks = self._rank_map(direct)
        entity_ranks = self._rank_map(entity)
        direct_scores = {
            str(memory["id"]): float(memory.get("relevance_score") or 0.0)
            for memory in direct
        }
        ranked = []
        for memory_id, memory in candidates.items():
            rrf = self._rrf_score(
                memory_id,
                direct_ranks=direct_ranks,
                entity_ranks=entity_ranks,
                graph_ranks=graph_ranks,
            )
            direct_score = direct_scores.get(memory_id, 0.0)
            graph_score = graph_scores.get(memory_id, 0.0)
            channels = []
            if memory_id in direct_ranks:
                channels.append("direct")
                hybrid_score = min(1.0, direct_score + (0.12 * rrf))
            elif memory_id in entity_ranks:
                channels.append("entity")
                hybrid_score = min(0.75, max(0.52, direct_score) + (0.12 * rrf))
            else:
                hybrid_score = min(0.55, (0.5 * graph_score) + (0.15 * rrf))
            if memory_id in entity_ranks and "entity" not in channels:
                channels.append("entity")
            if memory_id in graph_ranks:
                channels.append("graph")

            result = dict(memory)
            result["relevance_score"] = round(clamp_score(hybrid_score), 6)
            result["retrieval_channels"] = channels
            result["retrieval_factors"] = {
                "direct_score": round(direct_score, 6),
                "direct_rank": direct_ranks.get(memory_id),
                "entity_rank": entity_ranks.get(memory_id),
                "graph_rank": graph_ranks.get(memory_id),
                "graph_score": round(graph_score, 8),
                "rrf_score": round(rrf, 6),
                "seed_specificity": (
                    round(seed_specificity[memory_id], 6)
                    if memory_id in seed_specificity
                    else None
                ),
            }
            result["ranking_explanation"] = [
                f"hybrid channels: {', '.join(channels) or 'none'}",
                f"direct={direct_score:.3f}, graph={graph_score:.3f}, rrf={rrf:.3f}",
            ]
            ranked.append(result)

        ranked.sort(
            key=lambda item: (
                -float(item.get("relevance_score") or 0.0),
                direct_ranks.get(str(item.get("id")), 10_000),
                graph_ranks.get(str(item.get("id")), 10_000),
                str(item.get("id") or ""),
            )
        )
        return ranked[: max(1, min(int(limit), 200))]
