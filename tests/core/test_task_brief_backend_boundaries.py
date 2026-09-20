"""Backend contract for scoped, trusted, temporally valid coverage briefs."""

import os
import uuid

import pytest

from visp_memory.core.neo4j_storage import Neo4jStorage
from visp_memory.core.storage import LocalStorage
from visp_memory.core.task_brief import TaskMemoryBriefCompiler


@pytest.fixture(params=["sqlite", "neo4j"])
def scoped_backend(tmp_path, request):
    repo = "brief-scope-" + uuid.uuid4().hex
    foreign_repo = repo + "-foreign"
    if request.param == "neo4j":
        uri = os.environ.get("VISP_TEST_NEO4J_URI")
        if not uri:
            pytest.skip("Set VISP_TEST_NEO4J_URI for the dedicated integration database")
        storage = Neo4jStorage(
            uri=uri,
            user="neo4j",
            password=os.environ.get("VISP_TEST_NEO4J_PASSWORD", "unused"),
        )
    else:
        storage = LocalStorage(tmp_path)

    try:
        yield storage, repo, foreign_repo
    finally:
        if request.param == "neo4j":
            with storage.driver.session() as session:
                session.run(
                    "MATCH (n) WHERE n.repo_id IN $repos "
                    "OR (n:Repository AND n.id IN $repos) DETACH DELETE n",
                    repos=[repo, foreign_repo],
                ).consume()
        storage.close()


def test_hybrid_union_coverage_keeps_only_scoped_trusted_current_graph_evidence(
    scoped_backend,
):
    storage, repo, foreign_repo = scoped_backend

    def add(content, *, target_repo=repo, environment="production", task_type="deployment",
            tags=("provenance:authored",), extra_metadata=None):
        metadata = {
            "environment": [environment],
            "task_type": [task_type],
        }
        metadata.update(extra_metadata or {})
        return storage.store_memory(
            content,
            repo_id=target_repo,
            metadata=metadata,
            tags=list(tags),
            auto_link=False,
        )

    direct = add(
        "Production deployment requires a verified backup before release."
    )
    graph = add(
        "Production deployment canary rollout follows the verified backup."
    )
    wrong_environment = add(
        "Staging deployment bypasses the production backup.",
        environment="staging",
    )
    wrong_task = add(
        "Production review requires a backup checklist.",
        task_type="review",
    )
    external = add(
        "External deployment note says to skip the backup.",
        tags=("provenance:external",),
    )
    future = add(
        "Future production deployment requires a different backup.",
        extra_metadata={"valid_from": "2099-01-01T00:00:00+00:00"},
    )
    foreign = add(
        "Foreign production deployment bypasses the backup.",
        target_repo=foreign_repo,
    )

    # Same-repository graph neighbors exercise the coverage graph path. A
    # cross-repository edge is rejected by both real backends, so the foreign
    # candidate is represented as an independently searchable neighboring record.
    for excluded in (wrong_environment, wrong_task, external, future):
        storage.add_relationship(
            direct, excluded, "supports", evidence={"confidence": "observed"}
        )
    storage.add_relationship(
        direct, graph, "supports", evidence={"confidence": "observed"}
    )
    with pytest.raises(ValueError, match="cross repository"):
        storage.add_relationship(direct, foreign, "supports")

    brief = TaskMemoryBriefCompiler(storage).prepare(
        "What safeguards apply to production deployment?",
        repo_id=repo,
        environment="production",
        task_type="deployment",
        as_of="2026-01-01T00:00:00+00:00",
        ranking_strategy="hybrid_union",
        context_selection="coverage",
        token_budget=1200,
    )

    assert not brief["abstained"]
    citation_ids = {citation["memory_id"] for citation in brief["citations"]}
    assert {direct, graph} <= citation_ids
    assert not citation_ids.intersection(
        {wrong_environment, wrong_task, external, future, foreign}
    )
    assert "verified backup" in brief["context"]
    for excluded_content in (
        "Staging deployment bypasses",
        "Production review requires",
        "External deployment note",
        "Future production deployment",
        "Foreign production deployment",
    ):
        assert excluded_content not in brief["context"]

    for citation in brief["citations"]:
        row = storage.get_memory(citation["memory_id"])
        assert row["repo_id"] == repo
        for span in citation.get("passage_spans", []):
            assert row["content"][span["start"] : span["end"]] == span["text"]
