"""Backend-portable hybrid retrieval with bounded associative graph recall."""

from __future__ import annotations

import math
import re
from collections import defaultdict
from collections.abc import Callable
from typing import Any, Optional

from visp_memory.core.code_graph import (
    PROXIMITY_DECAY,
    PROXIMITY_MAX_HOPS,
    PROXIMITY_MIN_ADMISSION,
    FileGraph,
    normalize_path,
)
from visp_memory.core.ranking import clamp_score, graph_edge_score, rank_memory_results

PPR_DAMPING = 0.5
PPR_ITERATIONS = 20
PPR_TOLERANCE = 1e-8
RRF_K = 60
MAX_CORPUS_MEMORIES = 500
MAX_GRAPH_EDGES = 2000
MAX_SEEDS = 8
DIRECT_MIN_SCORE = 0.16
DIRECT_WITH_ENTITY_SCOPE_MIN_SCORE = 0.30
# The score an exact file/symbol match has always carried in the entity channel. A
# memory admitted for structural proximity alone is capped strictly below it, so no
# structural admission can ever outrank an identity match. See _structural_score.
ENTITY_EXACT_FLOOR = 0.52
STRUCTURAL_SCORE_CEILING = 0.51
# How many memories one retrieval may admit on structural grounds alone. This is the
# bounded share, and it is a literal here rather than a number arriving from intel: the
# projection states which files are adjacent, and Memory alone decides how much that is
# worth. Three, because the damage of a wrong seed is then three memories rather than a
# neighbourhood, and because an admission is only useful if a human can see why it is
# there. Admissions are *added* to the result, never swapped for something in it.
STRUCTURAL_MAX_ADMISSIONS = 3

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
    """Fuse direct, code-entity, and associative graph rankings.

    ``code_graph`` is optional and read-only. When it is absent -- unconfigured, stale,
    malformed, or simply not passed -- every result is byte-identical to what this
    retriever returned before structural conditioning existed. When it is present it
    can do exactly one thing: **add** at most :data:`STRUCTURAL_MAX_ADMISSIONS` memories
    that are attached to files within two import/test hops of the query's files and
    that no other channel reached, scored strictly below every identity match.

    Stated as the three properties it has to satisfy, because they are the difference
    between a signal that informs and one that decides:

    * **Monotone.** The returned list is a superset of the list this same call returns
      with ``code_graph=None``. Admissions are counted against their own budget, not
      against the window, so structure cannot push a memory off the bottom of a result.
    * **Floored.** Every slot the other channels earned stays theirs; admissions are
      capped at :data:`STRUCTURAL_SCORE_CEILING`, below :data:`ENTITY_EXACT_FLOOR`, so
      "one import away" is never presented above "about this file".
    * **Bounded.** Three admissions, two hops, and a proximity floor of
      :data:`~visp_memory.core.code_graph.PROXIMITY_MIN_ADMISSION` -- all literals in
      Memory's own source, none of them arriving from intel.

    It also cannot re-score a memory another channel found (admissions rank in a
    disjoint space, so no other memory's rrf moves) and cannot seed the associative
    walk.
    """

    def __init__(self, storage, code_graph: Optional[FileGraph] = None):
        self.storage = storage
        self.code_graph = code_graph

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
    def _memory_files(memory: dict[str, Any]) -> list[str]:
        metadata = memory.get("metadata") or {}
        return [
            normalize_path(value)
            for value in (metadata.get("files") or metadata.get("applies_to") or [])
            if normalize_path(value)
        ]

    @classmethod
    def _structural_proximity_of(
        cls, memory: dict[str, Any], proximity: dict[str, float]
    ) -> float:
        """How near this memory's files sit to the query's files. 0.0 when not near.

        Full-path equality only. The basename shortcut that :mod:`visp_memory.core.anchors`
        uses for rename tolerance is a false-match generator here, and a wrong edge
        that expands a wrong neighbourhood is the one failure mode this signal has.
        """
        if not proximity:
            return 0.0
        return max((proximity.get(path, 0.0) for path in cls._memory_files(memory)), default=0.0)

    @staticmethod
    def _hops_for(proximity: float) -> int:
        """Report the hop count a proximity score stands for, for the audit line."""
        hops = 0
        score = 1.0
        while score > proximity + 1e-9 and hops < PROXIMITY_MAX_HOPS:
            score *= PROXIMITY_DECAY
            hops += 1
        return hops

    def _short_snapshot(self) -> str:
        snapshot = self.code_graph.snapshot_id if self.code_graph is not None else ""
        return snapshot[-12:] if snapshot else "unknown"

    @staticmethod
    def _structural_score(proximity: float, rrf: float) -> float:
        """Score for a memory admitted by structure alone.

        Capped at :data:`STRUCTURAL_SCORE_CEILING`, below the exact-match floor of
        :data:`ENTITY_EXACT_FLOOR`, so "one import away" can never be presented as
        "about this file".
        """
        return min(STRUCTURAL_SCORE_CEILING, (ENTITY_EXACT_FLOOR * proximity) + (0.12 * rrf))

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
        environment: Any = None,
        task_type: Any = None,
        as_of: Any = None,
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
                environment=environment,
                task_type=task_type,
                as_of=as_of,
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

        # Structural conditioning, part one: who is near. The query's files are the
        # seeds and intel's projection says which files sit one or two import/test hops
        # away. Nothing is admitted here -- admission happens once the text and
        # associative channels have finished, so that structure can be given exactly
        # the memories those channels did not already find.
        proximity_map = (
            self.code_graph.structural_proximity(files)
            if (self.code_graph is not None and files)
            else {}
        )
        exact_ids = {str(memory["id"]) for memory in entity_candidates}
        nearby: dict[str, tuple[float, dict[str, Any]]] = {}
        if proximity_map:
            for memory in corpus:
                memory_id = str(memory.get("id") or "")
                if not memory_id or memory_id in exact_ids:
                    continue
                score = self._structural_proximity_of(memory, proximity_map)
                if score >= PROXIMITY_MIN_ADMISSION:
                    nearby[memory_id] = (score, memory)

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
        # The entity channel is identity matches and nothing else, exactly as it was
        # before a graph existed. Structural admissions get their own rank space below,
        # so no memory the text channel found has its rrf -- and therefore its score --
        # moved by the presence of a projection.
        entity_exact = rank_memory_results(entity_candidates, query=query, limit=60)
        corpus_terms = [_terms(str(memory.get("content") or "")) for memory in corpus]
        query_terms = _terms(query)
        seeds = {}
        seed_specificity = {}
        direct_seed_ids = {str(memory["id"]) for memory in direct}
        seed_candidates = list(direct[:5])
        selected_seed_ids = {str(memory["id"]) for memory in seed_candidates}
        # Seeded from exact matches only. Letting a structural admission seed the
        # associative walk would move the graph scores of memories the text channel
        # already found -- structure would be re-scoring results it did not admit,
        # which is the one thing this signal is not allowed to do.
        seed_candidates.extend(
            memory for memory in entity_exact if str(memory["id"]) not in selected_seed_ids
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
        entity_ranks = self._rank_map(entity_exact)
        direct_scores = {
            str(memory["id"]): float(memory.get("relevance_score") or 0.0)
            for memory in direct
        }

        # Structural conditioning, part two: admission. A memory is admitted on
        # structural grounds only if no other channel reached it -- not direct, not an
        # identity match, not on the associative walk. Everything the other channels
        # found is scored exactly as it was before the projection existed, so the
        # returned set is a superset of the no-graph set and structure is incapable of
        # taking a memory away. At most STRUCTURAL_MAX_ADMISSIONS are added, strongest
        # hop first with ties broken by id.
        eligible = [
            (score, memory_id, memory)
            for memory_id, (score, memory) in nearby.items()
            if memory_id not in direct_ranks
            and memory_id not in exact_ids
            and memory_id not in graph_memory_ids
        ]
        # Nearer first; among equally near memories, the ones the query's words also
        # like. Text is the tie-break rather than the admission rule -- none of these
        # cleared the direct channel's threshold, or they would not be here -- and it
        # exists because deciding which three of twenty equally-adjacent memories to
        # admit by identifier order is deciding it by nothing.
        text_rank = {
            str(memory["id"]): index
            for index, memory in enumerate(
                rank_memory_results([item[2] for item in eligible], query=query, limit=200)
            )
        }
        structural_scores: dict[str, float] = {}
        for score, memory_id, memory in sorted(
            eligible,
            key=lambda item: (-item[0], text_rank.get(item[1], len(text_rank)), item[1]),
        )[:STRUCTURAL_MAX_ADMISSIONS]:
            structural_scores[memory_id] = score
            candidates[memory_id] = memory
        # Admissions rank among themselves, in their own space, below the identity
        # matches they may never displace. Keys here are disjoint from entity_ranks by
        # construction, so passing this map instead leaves every other memory's rrf
        # bit-identical to the no-graph run.
        structural_ranks = {
            memory_id: index
            for index, memory_id in enumerate(
                sorted(structural_scores, key=lambda key: (-structural_scores[key], key)),
                start=len(entity_ranks) + 1,
            )
        }

        ranked = []
        for memory_id, memory in candidates.items():
            structural = structural_scores.get(memory_id, 0.0)
            rrf = self._rrf_score(
                memory_id,
                direct_ranks=direct_ranks,
                entity_ranks=structural_ranks if structural else entity_ranks,
                graph_ranks=graph_ranks,
            )
            direct_score = direct_scores.get(memory_id, 0.0)
            graph_score = graph_scores.get(memory_id, 0.0)
            channels = []
            if memory_id in direct_ranks:
                channels.append("direct")
                hybrid_score = min(1.0, direct_score + (0.12 * rrf))
            elif memory_id in exact_ids:
                channels.append("entity")
                hybrid_score = min(0.75, max(ENTITY_EXACT_FLOOR, direct_score) + (0.12 * rrf))
            elif structural:
                hybrid_score = self._structural_score(structural, rrf)
            else:
                hybrid_score = min(0.55, (0.5 * graph_score) + (0.15 * rrf))
            if memory_id in exact_ids and "entity" not in channels:
                channels.append("entity")
            if structural:
                channels.append("structure")
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
            if structural:
                # Labelled, always. A memory surfaced because of an import edge is
                # being surfaced on a snapshot's authority, and a structural claim that
                # arrives unmarked is indistinguishable from one about this file.
                hops = self._hops_for(structural)
                result["retrieval_factors"]["structural_proximity"] = round(structural, 6)
                result["retrieval_factors"]["structural_hops"] = hops
                result["retrieval_factors"]["code_graph_snapshot"] = (
                    self.code_graph.snapshot_id if self.code_graph is not None else None
                )
                result["ranking_explanation"].append(
                    f"structural proximity {structural:.2f} "
                    f"({hops} import/test hop{'s' if hops != 1 else ''} from the task's files, "
                    f"per intel snapshot {self._short_snapshot()})"
                )
            ranked.append(result)

        def _order(item: dict[str, Any]) -> tuple[Any, ...]:
            return (
                -float(item.get("relevance_score") or 0.0),
                direct_ranks.get(str(item.get("id")), 10_000),
                graph_ranks.get(str(item.get("id")), 10_000),
                str(item.get("id") or ""),
            )

        ranked.sort(key=_order)
        window = max(1, min(int(limit), 200))
        if not structural_scores:
            return ranked[:window]

        # Truncate the pre-existing channels against the window on their own, then add
        # the structural admissions back. The alternative -- one sort and one slice --
        # would let an admission push a memory off the bottom of the window, which is
        # structure deciding what recall may not return. Result: everything the no-graph
        # call returned, plus at most STRUCTURAL_MAX_ADMISSIONS, in score order.
        baseline = [item for item in ranked if str(item.get("id") or "") not in structural_scores]
        admitted = [item for item in ranked if str(item.get("id") or "") in structural_scores]
        kept = baseline[:window] + admitted
        kept.sort(key=_order)
        return kept
