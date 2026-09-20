"""Reflection keeps source scope, provenance, and retrieval lineage intact."""

import os
import uuid

import pytest

from visp_memory.config import LLMConfig
from visp_memory.core.model_router import ModelRouter
from visp_memory.core.neo4j_storage import Neo4jStorage
from visp_memory.core.reflection import ReflectionEngine
from visp_memory.core.storage import EvidenceReferenceError, LocalStorage
from visp_memory.core.trust import Provenance, provenance_of


def _engine(storage):
    return ReflectionEngine(storage, ModelRouter(LLMConfig(provider="none")))


@pytest.fixture
def local_reflection_storage(tmp_path):
    storage = LocalStorage(tmp_path / "reflection", embedding_fn=lambda _: [1.0])
    try:
        yield storage
    finally:
        storage.close()


def test_reflection_preserves_source_runtime_scope(local_reflection_storage):
    storage = local_reflection_storage
    sources = [
        storage.store_memory(
            f"Production audit rule {index}: require billing approval.",
            repo_id="audit",
            metadata={"environment": ["production"], "task_type": ["deployment"]},
            tags=["provenance:authored"],
            auto_link=False,
        )
        for index in range(2)
    ]

    result = _engine(storage).materialize(
        repo_id="audit",
        title="Production approval",
        evidence_ids=sources,
        actor_id="audit",
    )

    row = storage.get_memory(result["id"])
    assert row["metadata"]["environment"] == ["production"]
    assert row["metadata"]["task_type"] == ["deployment"]


def test_reflection_does_not_promote_unknown_source_provenance(local_reflection_storage):
    storage = local_reflection_storage
    sources = [
        storage.store_memory(
            f"Imported audit claim {index}.",
            repo_id="audit",
            tags=["provenance:unknown"],
            auto_link=False,
        )
        for index in range(2)
    ]

    assert all(provenance_of(storage.get_memory(mid)) is Provenance.UNKNOWN for mid in sources)
    result = _engine(storage).materialize(
        repo_id="audit", title="Imported claims", evidence_ids=sources, actor_id="audit"
    )

    assert provenance_of(storage.get_memory(result["id"])) is Provenance.UNKNOWN


def test_reflection_rejects_mixed_runtime_scopes(local_reflection_storage):
    storage = local_reflection_storage
    sources = [
        storage.store_memory(
            "Production deployment rule.",
            repo_id="audit",
            metadata={"environment": ["production"], "task_type": ["deployment"]},
            tags=["provenance:authored"],
            auto_link=False,
        ),
        storage.store_memory(
            "Staging deployment rule.",
            repo_id="audit",
            metadata={"environment": ["staging"], "task_type": ["deployment"]},
            tags=["provenance:authored"],
            auto_link=False,
        ),
    ]

    with pytest.raises(ValueError, match="Source scopes differ"):
        _engine(storage).materialize(
            repo_id="audit", title="Mixed deployment", evidence_ids=sources, actor_id="audit"
        )


def test_reflection_rejects_missing_lineage_source(local_reflection_storage):
    storage = local_reflection_storage

    with pytest.raises(EvidenceReferenceError, match="missing or deleted"):
        _engine(storage).materialize(
            repo_id="audit", title="Broken", evidence_ids=["missing"], actor_id="audit"
        )


def test_reflection_rejects_cross_repository_graph_bridge(local_reflection_storage):
    storage = local_reflection_storage
    source = storage.store_memory(
        "A foreign repository rule.",
        repo_id="other-repo",
        tags=["provenance:authored"],
        auto_link=False,
    )
    with pytest.raises(EvidenceReferenceError, match="same repository"):
        _engine(storage).materialize(
            repo_id="audit", title="Foreign bridge", evidence_ids=[source], actor_id="audit"
        )


def test_reflection_is_retrievable_after_materialization_and_restart(tmp_path):
    data_dir = tmp_path / "reflection-restart"
    storage = LocalStorage(data_dir, embedding_fn=lambda _: [1.0])
    source = storage.store_memory(
        "Production deployments require billing approval.",
        repo_id="audit",
        metadata={"environment": ["production"], "task_type": ["deployment"]},
        tags=["provenance:authored"],
        auto_link=False,
    )
    source_two = storage.store_memory(
        "Billing approval is recorded before production deployment.",
        repo_id="audit",
        metadata={"environment": ["production"], "task_type": ["deployment"]},
        tags=["provenance:authored"],
        auto_link=False,
    )
    reflected = _engine(storage).materialize(
        repo_id="audit",
        title="Billing deployment approval",
        evidence_ids=[source, source_two],
        actor_id="audit",
    )
    storage.close()

    reopened = LocalStorage(data_dir, embedding_fn=lambda _: [1.0])
    try:
        matches = reopened.search_memories(
            "billing deployment approval", repo_id="audit", layer="semantic"
        )
        row = next(item for item in matches if item["id"] == reflected["id"])
        assert row["source_ids"] == [source, source_two]
        assert row["metadata"]["environment"] == ["production"]
    finally:
        reopened.close()


def test_reflection_is_retrievable_after_materialization_and_restart_on_neo4j():
    uri = os.environ.get("VISP_TEST_NEO4J_URI")
    if not uri:
        pytest.skip("Requires disposable Neo4j")

    repo_id = f"reflection-integrity-neo4j-{uuid.uuid4().hex}"
    storage = Neo4jStorage(uri=uri, user="neo4j", password="unused")
    try:
        with storage.driver.session() as session:
            session.run("MATCH (n) WHERE n.repo_id = $repo DETACH DELETE n", repo=repo_id).consume()

        sources = [
            storage.store_memory(
                text,
                repo_id=repo_id,
                metadata={"environment": ["production"], "task_type": ["deployment"]},
                tags=["provenance:authored"],
                auto_link=False,
            )
            for text in (
                "Production deployments require billing approval.",
                "Billing approval is recorded before production deployment.",
            )
        ]
        reflected = _engine(storage).materialize(
            repo_id=repo_id,
            title="Billing deployment approval",
            evidence_ids=sources,
            actor_id="audit",
        )
        storage.close()

        reopened = Neo4jStorage(uri=uri, user="neo4j", password="unused")
        try:
            matches = reopened.search_memories(
                "billing deployment approval", repo_id=repo_id, layer="semantic"
            )
            row = next(item for item in matches if item["id"] == reflected["id"])
            assert row["source_ids"] == sources
            assert row["metadata"]["environment"] == ["production"]
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
