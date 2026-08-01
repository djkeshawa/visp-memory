"""Tests for spreading-activation graph recall (converging evidence paths)."""

from pathlib import Path

import pytest

from visp_memory.core.storage import LocalStorage
from visp_memory.core.trust import Provenance, provenance_tag
from visp_memory.recall.graph import GraphRecall, spread_activation


def _edge(source: str, target: str, score: float = 0.8) -> dict:
    return {
        "source_id": source,
        "target_id": target,
        "relevance_factors": {"edge_score": score},
    }


class TestSpreadActivationFunction:
    def test_converging_paths_accumulate(self):
        # Diamond: S -> A -> X and S -> B -> X, plus a single chain S -> C -> Y.
        # X (two converging paths) must out-activate Y (one path, same length).
        edges = [
            _edge("S", "A"),
            _edge("S", "B"),
            _edge("A", "X"),
            _edge("B", "X"),
            _edge("S", "C"),
            _edge("C", "Y"),
        ]
        activation = spread_activation({"S": 1.0}, edges)

        assert activation["X"] > activation["Y"]
        assert activation["S"] == 1.0

    def test_activation_attenuates_with_distance(self):
        chain = [_edge("S", "A"), _edge("A", "B"), _edge("B", "C")]
        activation = spread_activation({"S": 1.0}, chain)
        assert activation["S"] > activation["A"] > activation["B"] > activation["C"]

    def test_activation_is_bounded_and_convergent(self):
        # A tight cycle with strong edges must not blow past 1.0.
        edges = [_edge("A", "B", 1.0), _edge("B", "C", 1.0), _edge("C", "A", 1.0)]
        activation = spread_activation({"A": 1.0, "B": 1.0, "C": 1.0}, edges, iterations=50)
        assert all(0.0 <= value <= 1.0 for value in activation.values())

    def test_deterministic(self):
        edges = [
            _edge("S", "A", 0.7),
            _edge("S", "B", 0.9),
            _edge("A", "X", 0.6),
            _edge("B", "X", 0.8),
        ]
        first = spread_activation({"S": 1.0}, edges)
        second = spread_activation({"S": 1.0}, edges)
        assert first == second

    def test_ignores_self_loops_and_malformed_edges(self):
        edges = [
            _edge("S", "S"),
            {"source_id": None, "target_id": "A", "relevance_factors": {"edge_score": 0.9}},
            _edge("S", "A"),
        ]
        activation = spread_activation({"S": 1.0}, edges)
        assert activation["S"] == 1.0
        assert "A" in activation


@pytest.fixture
def storage(tmp_path: Path) -> LocalStorage:
    return LocalStorage(tmp_path)


class TestGraphRecallActivation:
    def _seed_diamond(self, storage: LocalStorage) -> dict[str, str]:
        """Seed: query hits `seed`; `hub` is reachable via two evidence paths,
        `leaf` via one path of the same length."""
        def store(content: str) -> str:
            return storage.store_memory(
                content,
                repo_id="repo-a",
                tags=[provenance_tag(Provenance.DERIVED)],
                auto_link=False,
            )

        ids = {
            "seed": store("auth token refresh strategy"),
            "mid_a": store("token cache invalidation notes"),
            "mid_b": store("refresh endpoint rate limits"),
            "hub": store("session revocation design"),
            "mid_c": store("logging conventions"),
            "leaf": store("dashboard color palette"),
        }
        pairs = [
            ("seed", "mid_a"),
            ("seed", "mid_b"),
            ("mid_a", "hub"),
            ("mid_b", "hub"),
            ("seed", "mid_c"),
            ("mid_c", "leaf"),
        ]
        for source, target in pairs:
            storage.add_relationship(
                ids[source],
                ids[target],
                "supports",
                strength=0.8,
                evidence={"confidence": "observed", "confidence_score": 0.8, "reason": "test"},
            )
        return ids

    def test_trace_ranks_convergent_node_above_single_path_node(self, storage):
        ids = self._seed_diamond(storage)
        result = GraphRecall(storage).trace(
            query="auth token refresh strategy",
            repo_id="repo-a",
            depth=2,
            token_budget=10000,
            limit=5,
        )

        by_id = {node["id"]: node for node in result["nodes"]}
        assert ids["hub"] in by_id and ids["leaf"] in by_id
        hub = by_id[ids["hub"]]
        leaf = by_id[ids["leaf"]]
        assert hub["relevance_factors"]["activation"] > leaf["relevance_factors"]["activation"]
        assert hub["relevance_score"] > leaf["relevance_score"]

    def test_neighbors_exposes_activation_factor(self, storage):
        ids = self._seed_diamond(storage)
        result = GraphRecall(storage).neighbors(
            memory_id=ids["seed"],
            repo_id="repo-a",
            depth=2,
            token_budget=10000,
            limit=25,
        )
        assert result["nodes"], "expected expanded neighborhood"
        for node in result["nodes"]:
            assert "activation" in node["relevance_factors"]
            assert 0.0 <= node["relevance_factors"]["activation"] <= 1.0
