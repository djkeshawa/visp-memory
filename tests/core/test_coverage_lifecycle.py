"""Coverage retains governed source lineage through maintenance and reopen."""

import os
import uuid

import pytest

from visp_memory.config import LLMConfig
from visp_memory.core.compression import MemoryCompressor
from visp_memory.core.model_router import ModelRouter
from visp_memory.core.neo4j_storage import Neo4jStorage
from visp_memory.core.reflection import ReflectionEngine
from visp_memory.core.storage import LocalStorage
from visp_memory.core.task_brief import TaskMemoryBriefCompiler


@pytest.mark.parametrize("backend", ["sqlite", "neo4j"])
def test_cited_coverage_after_compression_reflection_and_reopen(tmp_path, backend):
    if backend == "neo4j":
        uri = os.environ.get("VISP_TEST_NEO4J_URI")
        if not uri:
            pytest.skip("Requires disposable Neo4j")
        def factory():
            return Neo4jStorage(uri=uri, user="neo4j", password="unused")
    else:
        def factory():
            return LocalStorage(tmp_path)
    repo = "coverage-test-" + uuid.uuid4().hex
    storage = factory()
    try:
        sources = [
            storage.store_memory(
                text, repo_id=repo, layer="episodic", tags=["provenance:authored"], auto_link=False
            )
            for text in (
                "Cache credentials must never be stored in browser local storage.",
                "Cache credentials require HttpOnly session cookies.",
            )
        ]
        episodes = [storage.get_memory(mid) for mid in sources]
        compressor = MemoryCompressor(
            storage, lambda _: "Cache credentials require HttpOnly cookies."
        )
        compact = compressor.compress_episodes_to_semantic(episodes)
        reflection = ReflectionEngine(storage, ModelRouter(LLMConfig())).materialize(
            repo_id=repo,
            title="Credential handling",
            evidence_ids=sources,
            actor_id="test",
        )
        compressor.decay_old_memories()
        for mid in [compact, reflection["id"]]:
            derived = storage.get_memory(mid)
            assert set(derived["source_ids"]) == set(sources)
            assert derived["evidence_ids"]
        storage.close()
        storage = factory()
        result = TaskMemoryBriefCompiler(storage).prepare(
            "How should cache credentials be handled?",
            repo_id=repo,
            context_selection="coverage",
            token_budget=800,
        )
        assert not result["abstained"]
        assert "HttpOnly" in result["context"]
        for citation in result["citations"]:
            memory = storage.get_memory(citation["memory_id"])
            assert memory["repo_id"] == repo
            for span in citation.get("passage_spans", []):
                assert memory["content"][span["start"] : span["end"]] == span["text"]
        for mid in sources:
            assert storage.get_memory(mid)["content"]
    finally:
        if backend == "neo4j":
            with storage.driver.session() as session:
                session.run("MATCH (n) WHERE n.repo_id=$repo DETACH DELETE n", repo=repo).consume()
        storage.close()
