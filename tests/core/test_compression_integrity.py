"""Compression validates canonical source lineage before deriving memories."""

import os
import uuid

import pytest

from visp_memory.core.compression import MemoryCompressor
from visp_memory.core.neo4j_storage import Neo4jStorage
from visp_memory.core.storage import EvidenceReferenceError, LocalStorage
from visp_memory.core.trust import Provenance, provenance_of


def _source(
    storage,
    content,
    *,
    repo_id="audit",
    environment=None,
    task_type=None,
    provenance="unknown",
    layer="episodic",
):
    evidence_id = storage.store_evidence(
        content,
        repo_id=repo_id,
        evidence_type="test-source",
        provenance=provenance,
    )
    metadata = {}
    if environment is not None:
        metadata["environment"] = environment
    if task_type is not None:
        metadata["task_type"] = task_type
    return storage.store_memory(
        content,
        layer=layer,
        repo_id=repo_id,
        tags=[f"provenance:{provenance}"],
        metadata=metadata,
        evidence_ids=[evidence_id],
        auto_link=False,
    )


@pytest.fixture
def local_compression_storage(tmp_path):
    storage = LocalStorage(tmp_path / "compression", embedding_fn=lambda _: [1.0])
    try:
        yield storage
    finally:
        storage.close()


def test_episode_compression_uses_canonical_scope_content_and_weakest_provenance(
    local_compression_storage,
):
    storage = local_compression_storage
    first = _source(
        storage,
        "Canonical production billing rule.",
        environment="production",
        task_type="deployment",
        provenance="unknown",
    )
    second = _source(
        storage,
        "Canonical production approval rule.",
        environment="production",
        task_type="deployment",
        provenance="authored",
    )
    supplied = [
        {"id": first, "content": "spoofed source text", "repo_id": "audit", "metadata": {}},
        {"id": second, "content": "spoofed source text", "repo_id": "audit", "metadata": {}},
    ]

    calls = []
    result = MemoryCompressor(
        storage, llm_compress_fn=lambda contents: calls.append(contents) or "derived"
    ).compress_episodes_to_semantic(supplied)

    stored = storage.get_memory(result)
    assert calls == [
        ["Canonical production billing rule.", "Canonical production approval rule."]
    ]
    assert stored["metadata"]["environment"] == ["production"]
    assert stored["metadata"]["task_type"] == ["deployment"]
    assert provenance_of(stored) is Provenance.UNKNOWN
    assert stored["source_ids"] == [first, second]


def test_episode_compression_rejects_mixed_canonical_scopes_before_model(
    local_compression_storage,
):
    storage = local_compression_storage
    first = _source(storage, "Production rule.", environment="production")
    second = _source(storage, "Staging rule.", environment="staging")
    calls = []

    result = MemoryCompressor(
        storage, llm_compress_fn=lambda contents: calls.append(contents) or "derived"
    ).compress_episodes_to_semantic(
        [storage.get_memory(first), storage.get_memory(second)]
    )

    assert result is None
    assert calls == []


def test_compression_rejects_archived_canonical_sources_before_model(
    local_compression_storage,
):
    storage = local_compression_storage
    first = _source(storage, "Archived rule one.")
    second = _source(storage, "Active rule two.")
    storage.update_memory(first, status="archived")
    calls = []

    with pytest.raises(EvidenceReferenceError, match="active"):
        MemoryCompressor(
            storage, llm_compress_fn=lambda contents: calls.append(contents) or "derived"
        ).compress_episodes_to_semantic(
            [storage.get_memory(first), storage.get_memory(second)]
        )
    assert calls == []


def test_compression_rejects_missing_canonical_sources_before_model(
    local_compression_storage,
):
    calls = []
    with pytest.raises(EvidenceReferenceError, match="missing or deleted"):
        MemoryCompressor(
            local_compression_storage,
            llm_compress_fn=lambda contents: calls.append(contents) or "derived",
        ).compress_episodes_to_semantic(
            [
                {"id": "missing-source", "content": "spoofed", "repo_id": "audit"},
                {"id": "missing-source-2", "content": "spoofed", "repo_id": "audit"},
            ]
        )
    assert calls == []


def test_principle_compression_preserves_canonical_lineage_after_restart(tmp_path):
    data_dir = tmp_path / "compression-restart"
    storage = LocalStorage(data_dir, embedding_fn=lambda _: [1.0])
    sources = [
        _source(
            storage,
            f"Canonical deployment principle source {index}.",
            environment="production",
            task_type="deployment",
            provenance="unknown" if index == 0 else "authored",
            layer="semantic",
        )
        for index in range(3)
    ]
    result = MemoryCompressor(storage).compress_semantic_to_principle(
        [storage.get_memory(source_id) for source_id in sources]
    )
    storage.close()

    reopened = LocalStorage(data_dir, embedding_fn=lambda _: [1.0])
    try:
        stored = reopened.get_memory(result)
        assert stored["source_ids"] == sources
        assert stored["metadata"]["environment"] == ["production"]
        assert stored["metadata"]["task_type"] == ["deployment"]
        assert provenance_of(stored) is Provenance.UNKNOWN
        matches = reopened.search_memories(
            "Canonical deployment principle", repo_id="audit", layer="semantic"
        )
        assert any(row["id"] == result for row in matches)
    finally:
        reopened.close()


def test_episode_compression_source_preservation_after_restart_on_neo4j():
    uri = os.environ.get("VISP_TEST_NEO4J_URI")
    if not uri:
        pytest.skip("Requires disposable Neo4j")

    repo_id = f"compression-integrity-neo4j-{uuid.uuid4().hex}"
    storage = Neo4jStorage(uri=uri, user="neo4j", password="unused")
    try:
        with storage.driver.session() as session:
            session.run(
                "MATCH (n) WHERE n.repo_id = $repo DETACH DELETE n", repo=repo_id
            ).consume()
        sources = [
            _source(
                storage,
                f"Canonical Neo4j deployment source {index}.",
                repo_id=repo_id,
                environment="production",
                task_type="deployment",
                provenance="unknown" if index == 0 else "authored",
            )
            for index in range(2)
        ]
        result = MemoryCompressor(storage).compress_episodes_to_semantic(
            [storage.get_memory(source_id) for source_id in sources]
        )
        storage.close()

        reopened = Neo4jStorage(uri=uri, user="neo4j", password="unused")
        try:
            stored = reopened.get_memory(result)
            assert stored["source_ids"] == sources
            assert stored["metadata"]["environment"] == ["production"]
            assert provenance_of(stored) is Provenance.UNKNOWN
            matches = reopened.search_memories(
                "Canonical Neo4j deployment", repo_id=repo_id, layer="semantic"
            )
            assert any(row["id"] == result for row in matches)
        finally:
            with reopened.driver.session() as session:
                session.run(
                    "MATCH (n) WHERE n.repo_id = $repo DETACH DELETE n", repo=repo_id
                ).consume()
            reopened.close()
    finally:
        if getattr(storage, "driver", None) is not None:
            with storage.driver.session() as session:
                session.run(
                    "MATCH (n) WHERE n.repo_id = $repo DETACH DELETE n", repo=repo_id
                ).consume()
            storage.close()
