"""Live Neo4j coverage for the independent lexical/vector union path."""

import os
import uuid

import pytest

from visp_memory.core.neo4j_storage import Neo4jStorage
from visp_memory.core.task_brief import TaskMemoryBriefCompiler


@pytest.fixture
def neo_union_storage():
    uri = os.environ.get("VISP_TEST_NEO4J_URI")
    if not uri:
        pytest.skip("Set VISP_TEST_NEO4J_URI for the dedicated integration database")
    repo = "union-" + uuid.uuid4().hex
    storage = Neo4jStorage(
        uri=uri,
        user="neo4j",
        password=os.environ.get("VISP_TEST_NEO4J_PASSWORD", "unused"),
    )
    yield storage, repo
    with storage.driver.session() as session:
        session.run(
            "MATCH (n) WHERE n.repo_id IN $repos OR "
            "(n:Repository AND n.id IN $repos) DETACH DELETE n",
            repos=[repo, repo + "-foreign"],
        ).consume()
    storage.close()


def test_neo_union_lexical_channel_and_compiler_scope_are_combined(neo_union_storage):
    storage, repo = neo_union_storage
    allowed = storage.store_memory(
        "Invoice IN-2718 total is $417.", repo_id=repo,
        tags=["provenance:authored"], auto_link=False,
    )
    storage.store_memory(
        "Invoice IN-2718 total is $999.", repo_id=repo + "-foreign",
        tags=["provenance:authored"], auto_link=False,
    )
    storage.store_memory(
        "Invoice IN-2718 total is $888.", repo_id=repo, status="deleted",
        tags=["provenance:authored"], auto_link=False,
    )

    lexical = storage.search_memories(
        "IN-2718", repo_id=repo, retrieval_channel="lexical", limit=10
    )
    vector = storage.search_memories(
        "IN-2718", repo_id=repo, retrieval_channel="vector", limit=10
    )
    assert [row["id"] for row in lexical] == [allowed]
    assert vector == []

    brief = TaskMemoryBriefCompiler(storage).prepare(
        "What is invoice IN-2718 total?", repo_id=repo,
        ranking_strategy="hybrid_union", token_budget=500,
    )
    assert [citation["memory_id"] for citation in brief["citations"]] == [allowed]
    assert "$417" in brief["context"]
    assert "$999" not in brief["context"]
    assert "$888" not in brief["context"]
