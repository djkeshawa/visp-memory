"""Cross-backend source learning and scoped passage selection contracts."""

import os
import uuid

import pytest

from visp_memory import Memory, MemoryConfig
from visp_memory.core.task_brief import TaskMemoryBriefCompiler
from visp_memory.core.trust import WriteChannel


@pytest.fixture(params=["sqlite", "neo4j"])
def cohesive_memory(tmp_path, request):
    config = MemoryConfig(repo_id="cohesion-" + uuid.uuid4().hex)
    config.embedding.provider = "none"
    config.storage.data_dir = tmp_path
    config.storage.backend = request.param
    if request.param == "neo4j":
        uri = os.environ.get("VISP_TEST_NEO4J_URI")
        if not uri:
            pytest.skip("Requires disposable Neo4j")
        config.storage.neo4j_uri = uri
        config.storage.neo4j_password = os.environ.get("VISP_TEST_NEO4J_PASSWORD", "unused")
    with Memory(config=config) as memory:
        try:
            yield memory
        finally:
            if request.param == "neo4j":
                with memory._storage.driver.session() as session:
                    session.run(
                        "MATCH (n) WHERE n.repo_id=$repo OR (n:Repository AND n.id=$repo) "
                        "DETACH DELETE n", repo=config.repo_id,
                    ).consume()


def test_source_reconciliation_and_citation_resolution(cohesive_memory):
    memory = cohesive_memory
    fact = "The workshop requires a refundable booking deposit."
    sources = [memory.record(f"Session {n}: {fact}") for n in (1, 2)]
    ids = [memory.learn(fact, source_episodes=[source], _write_channel=WriteChannel.CONVERSATION)
           for source in sources]
    assert ids[0] == ids[1]
    belief = memory._storage.get_memory(ids[0])
    expected = {eid for source in sources
                for eid in memory._storage.get_memory(source)["evidence_ids"]}
    assert expected <= set(belief["evidence_ids"])
    edges = memory._storage.get_all_relationships(repo_id=memory.config.repo_id)
    assert all(any(e["source_id"] == source and e["target_id"] == ids[0]
                   and e["relationship"].casefold() == "derived_from" for e in edges)
               for source in sources)


def test_scope_and_trust_gate_direct_graph_and_passages(cohesive_memory):
    memory = cohesive_memory
    storage, repo = memory._storage, memory.config.repo_id
    keep = storage.store_memory(
        "Session date: 2024/05/01\nuser: Workshop cost was $25.", repo_id=repo,
        metadata={"environment": ["production"], "task_type": ["review"]},
        tags=["provenance:authored"], auto_link=False,
    )
    rejected = []
    for env, task, provenance in (
        ("staging", "review", "authored"),
        ("production", "implementation", "authored"),
        ("production", "review", "external"),
    ):
        other = storage.store_memory(
            f"user: Workshop cost forbidden {env} {task} {provenance} was $999.", repo_id=repo,
            metadata={"environment": [env], "task_type": [task]},
            tags=[f"provenance:{provenance}"], auto_link=False,
        )
        rejected.append(other)
        storage.add_relationship(keep, other, "related_to", strength=1.0)
    brief = TaskMemoryBriefCompiler(storage).prepare(
        "What was the workshop cost?", repo_id=repo, token_budget=500,
        environment=["production"], task_type=["review"], context_selection="coverage",
    )
    assert not brief["abstained"]
    assert "$25" in brief["context"]
    assert "$999" not in brief["context"]
    assert not (set(rejected) & {c["memory_id"] for c in brief["citations"]})
    for citation in brief["citations"]:
        row = storage.get_memory(citation["memory_id"])
        for span in citation["passage_spans"]:
            assert row["content"][span["start"]:span["end"]] == span["text"]


def test_learn_retains_source_scope_before_reconciliation(cohesive_memory):
    memory = cohesive_memory
    fact = "Workshop bookings require a refundable deposit."
    ids = []
    for environment in ("production", "staging"):
        source = memory.record(
            fact, context={"environment": [environment], "task_type": ["review"]}
        )
        mid = memory.learn(fact, source_episodes=[source])
        row = memory._storage.get_memory(mid)
        assert row["metadata"]["environment"] == [environment]
        assert row["metadata"]["task_type"] == ["review"]
        ids.append(mid)
    assert ids[0] != ids[1]


def test_quarantined_source_cannot_be_promoted_by_fact_preparation(cohesive_memory):
    from visp_memory.core.trust import filter_unsolicited

    memory = cohesive_memory
    fact = "The workshop booking requires a refundable deposit."
    trusted = memory.learn(fact, _write_channel=WriteChannel.CLI)
    source = memory.record(fact, _write_channel=WriteChannel.IMPORT)
    derived = memory.learn(fact, source_episodes=[source], _write_channel=WriteChannel.CONVERSATION)
    assert derived != trusted
    assert not filter_unsolicited([memory._storage.get_memory(derived)]).allowed
    with Memory(config=memory.config) as reopened:
        assert not filter_unsolicited([reopened._storage.get_memory(derived)]).allowed
        assert reopened._storage.get_memory(source)["content"] == fact


def test_mixed_scope_extraction_is_rejected_before_writing(cohesive_memory):
    memory = cohesive_memory
    sources = [memory.record("Workshop deposit.", context={"environment": [env]})
               for env in ("production", "staging")]
    before = memory._storage.list_memories(repo_id=memory.config.repo_id, layer="semantic")
    with pytest.raises(ValueError, match="Source scopes differ"):
        memory.learn("Workshop deposit.", source_episodes=sources)
    assert memory._storage.list_memories(repo_id=memory.config.repo_id, layer="semantic") == before
