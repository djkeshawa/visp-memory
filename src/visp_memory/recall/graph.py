"""Evidence-backed graph recall over portable memory storage APIs."""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any

from visp_memory.core.ranking import (
    clamp_score,
    graph_edge_score,
    graph_node_relevance,
    rank_memory_results,
    text_similarity,
)
from visp_memory.core.trust import TrustFilterResult, filter_unsolicited

# Spreading-activation constants (HippoRAG-style associative recall, cheap variant).
# Each hop attenuates the signal by ACTIVATION_HOP_DECAY; a damped fixed-point pass
# lets activation from multiple converging evidence paths ACCUMULATE on a node, so a
# memory reachable through several independent paths outranks one reachable through a
# single path of the same length. Decay < 1 with the seed-anchored update rule keeps
# the iteration bounded and convergent.
ACTIVATION_HOP_DECAY = 0.6
ACTIVATION_ITERATIONS = 3


def spread_activation(
    seeds: dict[str, float],
    edges: list[dict[str, Any]],
    decay: float = ACTIVATION_HOP_DECAY,
    iterations: int = ACTIVATION_ITERATIONS,
) -> dict[str, float]:
    """Spread activation from seed memories across an undirected evidence subgraph.

    Runs a damped Jacobi-style fixed point: each round, a node's activation is its
    seed value plus the decayed, edge-weighted activation of its neighbors from the
    previous round, capped at 1.0. Convergent paths sum — the associative-recall
    property plain BFS path-confidence lacks. Deterministic for a given input.
    """
    weights: dict[tuple[str, str], float] = {}
    for edge in edges:
        source = edge.get("source_id")
        target = edge.get("target_id")
        if not source or not target or source == target:
            continue
        factors = edge.get("relevance_factors") or {}
        score = clamp_score(
            factors.get("edge_score", edge.get("relevance_score")), default=0.5
        )
        key = (source, target) if source <= target else (target, source)
        weights[key] = max(weights.get(key, 0.0), score)

    activation = {node: clamp_score(value) for node, value in seeds.items()}
    for _ in range(max(1, int(iterations))):
        incoming: dict[str, float] = defaultdict(float)
        for (source, target), weight in weights.items():
            contribution = weight * decay
            if source in activation:
                incoming[target] += activation[source] * contribution
            if target in activation:
                incoming[source] += activation[target] * contribution
        next_activation = dict(activation)
        for node, inflow in incoming.items():
            next_activation[node] = min(1.0, seeds.get(node, 0.0) + inflow)
        if next_activation == activation:
            break
        activation = next_activation
    return activation


class GraphRecall:
    """Build compact recall subgraphs from existing memory relationships."""

    MAX_DEPTH = 4
    MAX_HOPS = 6
    MAX_EDGES_PER_NODE = 12

    def __init__(self, storage):
        self.storage = storage

    def neighbors(
        self,
        memory_id: str,
        relationship_filter: str | None = None,
        repo_id: str | None = None,
        depth: int = 1,
        token_budget: int = 2000,
        limit: int = 25,
    ) -> dict[str, Any]:
        relationships, omitted = self._trusted_relationships(
            self._relationships(repo_id, relationship_filter), repo_id
        )
        nodes, edges, expanded_omitted = self._expand(
            [memory_id], relationships, repo_id, depth, limit
        )
        omitted.extend(expanded_omitted)
        self._apply_activation(nodes, edges, {memory_id: 1.0})
        return self._result(
            mode="neighbors",
            nodes=nodes,
            edges=edges,
            omitted=omitted,
            token_budget=token_budget,
            depth=depth,
            limit=limit,
            explanation=f"Neighbors expanded from memory {memory_id}.",
        )

    def trace(
        self,
        query: str,
        repo_id: str | None = None,
        depth: int = 2,
        token_budget: int = 2000,
        limit: int = 5,
        relationship_filter: str | None = None,
    ) -> dict[str, Any]:
        seeds = self._search(query, repo_id, limit)
        seed_ids = [item["id"] for item in seeds if item.get("id")]
        relationships, omitted = self._trusted_relationships(
            self._relationships(repo_id, relationship_filter), repo_id
        )
        nodes, edges, expanded_omitted = self._expand(
            seed_ids, relationships, repo_id, depth, limit * 6
        )
        omitted.extend(expanded_omitted)

        seed_scores = {item["id"]: item.get("relevance_score", 0.0) for item in seeds}
        self._apply_activation(nodes, edges, {seed_id: 1.0 for seed_id in seed_ids})
        for node in nodes:
            node["relevance_factors"]["query_score"] = (
                seed_scores.get(node["id"]) or text_similarity(query, node.get("content", ""))
            )
            node["relevance_score"] = graph_node_relevance(
                query_score=node["relevance_factors"]["query_score"],
                importance=node.get("importance", 0.5),
                edge_score=node["relevance_factors"].get(
                    "activation", node["relevance_factors"].get("path_confidence", 1.0)
                ),
                distance=node["relevance_factors"].get("distance", 0),
            )

        nodes.sort(key=self._node_sort_key, reverse=True)
        return self._result(
            mode="trace",
            query=query,
            nodes=nodes,
            edges=edges,
            omitted=omitted,
            token_budget=token_budget,
            depth=depth,
            limit=limit,
            explanation="Trace recall expanded ranked seed memories through relationships.",
        )

    def path(
        self,
        source_id: str,
        target_id: str,
        repo_id: str | None = None,
        max_hops: int = 4,
        token_budget: int = 2000,
    ) -> dict[str, Any]:
        relationships, omitted = self._trusted_relationships(
            self._relationships(repo_id), repo_id
        )
        max_hops = self._bounded_int(max_hops, 1, self.MAX_HOPS)
        edge_path = self._shortest_edge_path(source_id, target_id, relationships, max_hops)
        if edge_path is None:
            edge_path = []
            omitted.append(
                {
                    "type": "path",
                    "count": 1,
                    "reason": f"No relationship path found within {max_hops} hops.",
                }
            )

        node_ids = [source_id]
        if edge_path:
            node_ids.append(target_id)
            for edge in edge_path:
                node_ids.extend([edge["source_id"], edge["target_id"]])
        nodes = self._nodes_for_ids(node_ids, repo_id, omitted=omitted)
        edges = [self._edge_payload(edge) for edge in edge_path]
        return self._result(
            mode="path",
            nodes=nodes,
            edges=edges,
            omitted=omitted,
            token_budget=token_budget,
            depth=max_hops,
            limit=len(nodes),
            explanation=f"Shortest relationship path from {source_id} to {target_id}.",
        )

    def why_relevant(
        self,
        query: str,
        memory_id: str,
        repo_id: str | None = None,
        depth: int = 2,
        token_budget: int = 2000,
        limit: int = 5,
    ) -> dict[str, Any]:
        seeds = self._search(query, repo_id, limit)
        seed_ids = [item["id"] for item in seeds if item.get("id")]
        relationships, omitted = self._trusted_relationships(
            self._relationships(repo_id), repo_id
        )

        if memory_id in seed_ids:
            nodes = self._nodes_for_ids(
                [memory_id], repo_id, query=query, omitted=omitted
            )
            edges = []
            explanation = "Memory is directly relevant to the query."
        else:
            path = self._first_path(seed_ids, memory_id, relationships, depth)
            if path:
                node_ids = [memory_id]
                for edge in path:
                    node_ids.extend([edge["source_id"], edge["target_id"]])
                nodes = self._nodes_for_ids(
                    node_ids, repo_id, query=query, omitted=omitted
                )
                edges = [self._edge_payload(edge) for edge in path]
                explanation = "Memory is relevant through an evidence-backed relationship path."
            else:
                nodes = self._nodes_for_ids(
                    [memory_id], repo_id, query=query, omitted=omitted
                )
                edges = []
                explanation = "No relationship path from query seeds was found."
                omitted.append(
                    {
                        "type": "path",
                        "count": 1,
                        "reason": f"No evidence path found within depth {depth}.",
                    }
                )

        return self._result(
            mode="why_relevant",
            query=query,
            nodes=nodes,
            edges=edges,
            omitted=omitted,
            token_budget=token_budget,
            depth=depth,
            limit=limit,
            explanation=explanation,
        )

    def _apply_activation(
        self,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        seeds: dict[str, float],
    ) -> None:
        """Attach spreading-activation scores and refresh node relevance in place.

        Converging evidence paths accumulate, so a memory linked to the seeds
        through several independent routes scores higher than single-path BFS
        confidence could express.
        """
        activation = spread_activation(seeds, edges)
        for node in nodes:
            factors = node.setdefault("relevance_factors", {})
            factors["activation"] = round(
                clamp_score(activation.get(node["id"], factors.get("path_confidence", 0.0))), 4
            )
            node["relevance_score"] = graph_node_relevance(
                query_score=1.0 if factors.get("seed") else factors.get("query_score", 0.0),
                importance=node.get("importance", 0.5),
                edge_score=factors["activation"],
                distance=factors.get("distance", 0),
            )

    def _search(
        self, query: str, repo_id: str | None, limit: int
    ) -> list[dict[str, Any]]:
        try:
            results = self.storage.search_memories(
                query=query,
                repo_id=repo_id,
                limit=limit,
                status="active",
            )
        except TypeError:
            results = self.storage.search_memories(query=query, repo_id=repo_id, limit=limit)
        return rank_memory_results(results, query=query, limit=limit, min_score=None)

    def _relationships(
        self, repo_id: str | None, relationship_filter: str | None = None
    ) -> list[dict[str, Any]]:
        relationships = self.storage.get_all_relationships(repo_id=repo_id)
        if relationship_filter:
            relationships = [
                item for item in relationships if item.get("relationship") == relationship_filter
            ]
        return sorted(
            relationships,
            key=lambda item: (
                str(item.get("source_id", "")),
                str(item.get("target_id", "")),
                str(item.get("relationship", "")),
            ),
        )

    def _trusted_relationships(
        self, relationships: list[dict[str, Any]], repo_id: str | None
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        node_ids = {
            memory_id
            for relationship in relationships
            for memory_id in (
                relationship.get("source_id"),
                relationship.get("target_id"),
            )
            if memory_id
        }
        memories = []
        for memory_id in sorted(node_ids):
            memory = self.storage.get_memory(memory_id)
            if memory and (repo_id is None or memory.get("repo_id") == repo_id):
                memories.append(memory)
        trust_filter = filter_unsolicited(memories)
        trusted_ids = {memory["id"] for memory in trust_filter.allowed}
        trusted_relationships = [
            relationship
            for relationship in relationships
            if relationship.get("source_id") in trusted_ids
            and relationship.get("target_id") in trusted_ids
        ]
        return trusted_relationships, self._trust_omissions(trust_filter)

    @staticmethod
    def _trust_omissions(result: TrustFilterResult) -> list[dict[str, Any]]:
        return [
            {"type": "trust", "count": 1, **rejection.as_dict()}
            for rejection in result.rejected
        ]

    def _expand(
        self,
        seed_ids: list[str],
        relationships: list[dict[str, Any]],
        repo_id: str | None,
        depth: int,
        limit: int,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        depth = self._bounded_int(depth, 0, self.MAX_DEPTH)
        limit = self._bounded_int(limit, 1, 200)
        omitted = []
        visited: dict[str, dict[str, Any]] = {}
        edge_map: dict[str, dict[str, Any]] = {}
        queue = deque((seed_id, 0, 1.0, True) for seed_id in seed_ids)

        while queue and len(visited) < limit:
            node_id, distance, path_confidence, is_seed = queue.popleft()
            if node_id in visited:
                continue
            previous_omissions = len(omitted)
            memory = self._memory(node_id, repo_id, omitted=omitted)
            if not memory:
                if len(omitted) == previous_omissions:
                    omitted.append(
                        {
                            "type": "node",
                            "count": 1,
                            "reason": (
                                f"Memory {node_id} was not found or is outside the repo scope."
                            ),
                        }
                    )
                continue
            visited[node_id] = self._node_payload(
                memory,
                distance=distance,
                path_confidence=path_confidence,
                seed=is_seed,
            )
            if distance >= depth:
                if self._neighbors(node_id, relationships):
                    omitted.append(
                        {
                            "type": "depth",
                            "count": 1,
                            "reason": f"Expansion stopped at depth {depth} for {node_id}.",
                        }
                    )
                continue

            neighbors = self._neighbors(node_id, relationships)
            if len(neighbors) > self.MAX_EDGES_PER_NODE:
                omitted.append(
                    {
                        "type": "high_degree",
                        "count": len(neighbors) - self.MAX_EDGES_PER_NODE,
                        "reason": f"High-degree memory {node_id} was capped.",
                    }
                )
            for relationship in neighbors[: self.MAX_EDGES_PER_NODE]:
                edge = self._edge_payload(relationship)
                edge_map.setdefault(edge["id"], edge)
                other_id = self._other_id(node_id, relationship)
                if other_id and other_id not in visited:
                    queue.append(
                        (
                            other_id,
                            distance + 1,
                            edge["relevance_factors"]["edge_score"],
                            False,
                        )
                    )

        if queue:
            omitted.append(
                {
                    "type": "limit",
                    "count": len(queue),
                    "reason": f"Node limit {limit} reached before traversal completed.",
                }
            )

        return list(visited.values()), list(edge_map.values()), omitted

    def _nodes_for_ids(
        self,
        memory_ids: list[str],
        repo_id: str | None,
        query: str | None = None,
        omitted: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        nodes = []
        seen = set()
        for index, memory_id in enumerate(memory_ids):
            if memory_id in seen:
                continue
            seen.add(memory_id)
            memory = self._memory(memory_id, repo_id, omitted=omitted)
            if memory:
                node = self._node_payload(memory, distance=index, path_confidence=1.0)
                if query:
                    node["relevance_factors"]["query_score"] = text_similarity(
                        query, node.get("content", "")
                    )
                    node["relevance_score"] = graph_node_relevance(
                        query_score=node["relevance_factors"]["query_score"],
                        importance=node.get("importance", 0.5),
                        edge_score=1.0,
                        distance=index,
                    )
                nodes.append(node)
        return nodes

    def _memory(
        self,
        memory_id: str,
        repo_id: str | None,
        omitted: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        memory = self.storage.get_memory(memory_id)
        if not memory:
            return None
        if repo_id is not None and memory.get("repo_id") != repo_id:
            return None
        trust_filter = filter_unsolicited([memory])
        if trust_filter.allowed:
            return trust_filter.allowed[0]
        if omitted is not None:
            omitted.extend(self._trust_omissions(trust_filter))
        return None

    def _neighbors(
        self, node_id: str, relationships: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        candidates = [
            item
            for item in relationships
            if item.get("source_id") == node_id or item.get("target_id") == node_id
        ]
        return sorted(
            candidates,
            key=lambda item: (
                graph_edge_score(item.get("strength"), item.get("evidence")),
                str(item.get("relationship", "")),
                str(item.get("source_id", "")),
                str(item.get("target_id", "")),
            ),
            reverse=True,
        )

    def _shortest_edge_path(
        self,
        source_id: str,
        target_id: str,
        relationships: list[dict[str, Any]],
        max_hops: int,
    ) -> list[dict[str, Any]] | None:
        if source_id == target_id:
            return []

        queue = deque([(source_id, [])])
        visited = {source_id}
        while queue:
            node_id, path = queue.popleft()
            if len(path) >= max_hops:
                continue
            for relationship in self._neighbors(node_id, relationships):
                other_id = self._other_id(node_id, relationship)
                if not other_id or other_id in visited:
                    continue
                next_path = [*path, relationship]
                if other_id == target_id:
                    return next_path
                visited.add(other_id)
                queue.append((other_id, next_path))
        return None

    def _first_path(
        self,
        seed_ids: list[str],
        target_id: str,
        relationships: list[dict[str, Any]],
        depth: int,
    ) -> list[dict[str, Any]] | None:
        for seed_id in seed_ids:
            path = self._shortest_edge_path(seed_id, target_id, relationships, depth)
            if path is not None:
                return path
        return None

    def _result(
        self,
        mode: str,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        omitted: list[dict[str, Any]],
        token_budget: int,
        depth: int,
        limit: int,
        explanation: str,
        query: str | None = None,
    ) -> dict[str, Any]:
        nodes, edges, budget_omitted = self._apply_token_budget(nodes, edges, token_budget)
        return {
            "mode": mode,
            "query": query,
            "nodes": nodes,
            "edges": edges,
            "omitted": [*omitted, *budget_omitted],
            "limits": {
                "depth": self._bounded_int(depth, 0, self.MAX_DEPTH),
                "token_budget": self._bounded_int(token_budget, 1, 100_000),
                "limit": limit,
            },
            "explanation": explanation,
        }

    def _apply_token_budget(
        self, nodes: list[dict[str, Any]], edges: list[dict[str, Any]], token_budget: int
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        budget = self._bounded_int(token_budget, 1, 100_000)
        omitted = []
        kept_nodes = []
        used = 0

        for node in nodes:
            cost = self._token_cost(node.get("content", "")) + 10
            if kept_nodes and used + cost > budget:
                omitted.append(
                    {
                        "type": "token_budget",
                        "count": len(nodes) - len(kept_nodes),
                        "reason": f"Token budget {budget} omitted lower-ranked nodes.",
                    }
                )
                break
            kept_nodes.append(node)
            used += cost

        if not kept_nodes and nodes:
            kept_nodes.append(nodes[0])
            omitted.append(
                {
                    "type": "token_budget",
                    "count": len(nodes) - 1,
                    "reason": f"Token budget {budget} kept only the highest-ranked node.",
                }
            )

        kept_ids = {node["id"] for node in kept_nodes}
        kept_edges = []
        for edge in edges:
            if edge["source_id"] not in kept_ids or edge["target_id"] not in kept_ids:
                continue
            cost = self._token_cost(edge.get("reason", "")) + 8
            if used + cost > budget:
                omitted.append(
                    {
                        "type": "token_budget",
                        "count": len(edges) - len(kept_edges),
                        "reason": f"Token budget {budget} omitted lower-ranked edges.",
                    }
                )
                break
            kept_edges.append(edge)
            used += cost

        return kept_nodes, kept_edges, omitted

    def _node_payload(
        self,
        memory: dict[str, Any],
        distance: int,
        path_confidence: float,
        seed: bool = False,
    ) -> dict[str, Any]:
        importance = clamp_score(memory.get("importance"), default=0.5)
        relevance_score = graph_node_relevance(
            query_score=1.0 if seed else 0.0,
            importance=importance,
            edge_score=path_confidence,
            distance=distance,
        )
        return {
            "id": memory["id"],
            "content": memory.get("content", ""),
            "layer": memory.get("layer", "episodic"),
            "category": memory.get("category"),
            "importance": importance,
            "repo_id": memory.get("repo_id"),
            "relevance_score": relevance_score,
            "relevance_factors": {
                "seed": seed,
                "distance": distance,
                "importance": importance,
                "path_confidence": clamp_score(path_confidence, default=0.5),
            },
        }

    def _edge_payload(self, relationship: dict[str, Any]) -> dict[str, Any]:
        evidence = relationship.get("evidence") or {}
        edge_score = graph_edge_score(relationship.get("strength"), evidence)
        source_id = relationship.get("source_id")
        target_id = relationship.get("target_id")
        rel_type = relationship.get("relationship", "related")
        reason = evidence.get("reason") or f"{rel_type} relationship links the memories."
        return {
            "id": f"{source_id}:{target_id}:{rel_type}",
            "source_id": source_id,
            "target_id": target_id,
            "relationship": rel_type,
            "strength": clamp_score(relationship.get("strength"), default=1.0),
            "evidence": evidence or None,
            "reason": reason,
            "relevance_score": edge_score,
            "relevance_factors": {
                "edge_score": edge_score,
                "strength": clamp_score(relationship.get("strength"), default=1.0),
                "confidence": evidence.get("confidence"),
                "confidence_score": clamp_score(
                    evidence.get("confidence_score"), default=edge_score
                ),
            },
        }

    @staticmethod
    def _other_id(node_id: str, relationship: dict[str, Any]) -> str | None:
        if relationship.get("source_id") == node_id:
            return relationship.get("target_id")
        if relationship.get("target_id") == node_id:
            return relationship.get("source_id")
        return None

    @staticmethod
    def _token_cost(value: Any) -> int:
        return max(1, len(str(value).split()))

    @staticmethod
    def _bounded_int(value: Any, minimum: int, maximum: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = minimum
        return max(minimum, min(maximum, parsed))

    @staticmethod
    def _node_sort_key(node: dict[str, Any]) -> tuple[float, float, str]:
        return (
            node.get("relevance_score", 0.0),
            node.get("importance", 0.0),
            str(node.get("id", "")),
        )
