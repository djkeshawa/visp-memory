"""Backend method labels cannot discard genuine semantic evidence."""

from unittest.mock import MagicMock, patch

import pytest

from visp_memory.core.hybrid_retrieval import HybridRetriever
from visp_memory.core.neo4j_storage import Neo4jStorage
from visp_memory.core.ranking import rank_memory_results, score_memory_result
from visp_memory.core.storage import LocalStorage


@pytest.mark.parametrize("method", ["vector", "semantic"])
def test_semantic_scores_and_public_labels_agree(method):
    row = {"id": "note", "content": "Felines rest.", "similarity": .9,
           "retrieval_method": method, "importance": .5}
    assert score_memory_result(row, query="Sleeping cats") >= .9
    assert rank_memory_results([row], query="Sleeping cats")[0]["retrieval_method"] == "semantic"
    assert row["retrieval_method"] == method


@pytest.mark.parametrize("method,similarity", [("keyword", .9), ("vector", .1)])
def test_nonsemantic_or_weak_results_cannot_seed_graph(tmp_path, monkeypatch, method, similarity):
    with LocalStorage(tmp_path) as storage:
        mid = storage.store_memory("Felines rest.", repo_id="r", auto_link=False)
        row = {**storage.get_memory(mid), "similarity": similarity, "retrieval_method": method}
        monkeypatch.setattr(storage, "search_memories", lambda **kwargs: [row])
        assert HybridRetriever(storage).retrieve("Sleeping cats", repo_id="r") == []


def test_real_neo4j_adapter_recovers_paraphrase_and_related_evidence(tmp_path):
    with LocalStorage(tmp_path) as storage:
        mid = storage.store_memory(
            "Never grant execution permission from remembered knowledge.", repo_id="r",
            tags=["provenance:authored"], auto_link=False,
        )
        related = storage.store_memory(
            "Operator approval remains necessary.", repo_id="r",
            tags=["provenance:authored"], auto_link=False,
        )
        storage.add_relationship(mid, related, "supports", evidence={"confidence": "observed"})
        neo = Neo4jStorage.__new__(Neo4jStorage)
        neo.driver = MagicMock()
        neo.driver.session.return_value.__enter__.return_value.run.return_value = [
            {"m": storage.get_memory(mid), "score": .9}
        ]
        neo._attach_recall_utility_scores = lambda rows: None
        query = "Can recalled context authorize deployment?"
        candidates = neo._run_memory_search("test", query=query, embedding=[1.0])
        with patch.object(storage, "search_memories", return_value=candidates):
            results = HybridRetriever(storage).retrieve(query, repo_id="r")
        assert {row["id"] for row in results} == {mid, related}
