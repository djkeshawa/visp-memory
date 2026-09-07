"""Real-server contracts. CI supplies a dedicated Neo4j database, never a user's store."""

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest

from visp_memory.core.clock import utc_now
from visp_memory.core.dreaming import Dreaming
from visp_memory.core.dreaming.persistence import transaction
from visp_memory.core.intent_workflow import IntentWorkflowReport
from visp_memory.core.neo4j_storage import Neo4jStorage
from visp_memory.core.storage import EvidenceImmutableError, EvidenceReferenceError


@pytest.fixture
def graph():
    uri = os.environ.get("VISP_TEST_NEO4J_URI")
    if not uri:
        pytest.skip("Set VISP_TEST_NEO4J_URI for the dedicated integration database")
    storage = Neo4jStorage(uri=uri, user="neo4j", password=os.environ["VISP_TEST_NEO4J_PASSWORD"])
    repo = "integration-" + uuid.uuid4().hex
    yield storage, repo
    # Only remove records created by this fixture, including its isolated second project.
    with storage.driver.session() as session:
        session.run(
            "MATCH (n) WHERE n.repo_id IN $repos OR "
            "(n:Repository AND n.id IN $repos) DETACH DELETE n",
            repos=[repo, repo + "-other"],
        ).consume()
    storage.close()


def test_capture_citations_rollback_and_reopen(graph):
    s, repo = graph
    raw = s.store_memory("The cache expires after five minutes", repo_id=repo, auto_link=False)
    belief = s.store_memory(
        "Cache TTL is five minutes",
        repo_id=repo,
        layer="semantic",
        source_ids=[raw],
        auto_link=False,
    )
    evidence = s.get_memory(belief)["evidence_ids"]
    assert evidence == s.get_memory(raw)["evidence_ids"]
    with pytest.raises(EvidenceImmutableError):
        s.store_evidence("Altered bytes", repo_id=repo, evidence_id=evidence[0])
    count = len(s.list_evidence(repo))
    with pytest.raises(EvidenceReferenceError):
        s.store_memory(
            "Must roll back capture", repo_id=repo, source_ids=["missing"], auto_link=False
        )
    assert len(s.list_evidence(repo)) == count
    with pytest.raises(EvidenceReferenceError):
        s.store_memory(
            "Cross-project belief",
            layer="semantic",
            repo_id=repo + "-other",
            evidence_ids=evidence,
            auto_link=False,
        )
    reopened = Neo4jStorage(uri=s.uri, user=s.user, password=s.password)
    try:
        assert reopened.get_memory(belief)["evidence_ids"] == evidence
        assert reopened.get_evidence(evidence[0])["content"].startswith("The cache")
        assert reopened.get_all_relationships(repo)[0]["source_id"] == raw
    finally:
        reopened.close()


def test_external_completion_is_ordered_and_bound(graph):
    s, repo = graph
    intent = s.set_intent("Ship the change", repo_id=repo, context={"workflow_history": ["forged"]})
    report = IntentWorkflowReport(
        source="assistant",
        task_id="task",
        event_id="done",
        revision=1,
        status="completed",
        summary="Tests passed",
        evidence=[{"description": "Verified in CI"}],
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: s.report_intent_workflow(
                    intent, report, actor_id="owner", channel="test"
                ),
                range(2),
            )
        )
    assert sum(r["applied"] for r in results) == 1
    s.update_intent(intent, context={"external_workflow": {"status": "active"}}, status="active")
    result = s.get_active_intents(repo, status="all")[0]
    assert result["status"] == "completed"
    assert len(result["context"]["workflow_history"]) == 1
    with pytest.raises(ValueError, match="different workflow"):
        s.report_intent_workflow(intent, report, actor_id="intruder", channel="test")


def test_dreaming_schedule_concurrency_undo_and_restart(graph):
    s, repo = graph
    ids = [
        s.store_memory("Identical cache configuration", repo_id=repo, auto_link=False)
        for _ in range(2)
    ]
    dream = Dreaming(s)
    assert dream.preview(repo)["proposals"][0]["automatic"]
    dream.configure(repo, enabled=True)
    with transaction(s, write=True) as unit:
        unit.put(
            "projects",
            {
                **dream._settings(unit, repo),
                "next_run": (utc_now() - timedelta(hours=1)).isoformat(),
            },
        )
    with ThreadPoolExecutor(max_workers=2) as pool:
        runs = list(
            pool.map(lambda _: dream.run(repo, actor_id="scheduler", scheduled=True), range(2))
        )
    assert sum(r is not None for r in runs) == 1
    report = next(r for r in runs if r)
    assert sum(s.get_memory(mid)["status"] == "merged" for mid in ids) == 1
    assert len(s.list_evidence(repo)) == 2
    reopened = Neo4jStorage(uri=s.uri, user=s.user, password=s.password)
    try:
        other = Dreaming(reopened)
        assert other.settings(repo)["enabled"]
        assert other.history(repo)[0]["id"] == report["id"]
        other.undo(repo, report["proposals"][0]["action_id"], actor_id="owner")
        assert all(s.get_memory(mid)["status"] == "active" for mid in ids)
        assert not other.preview(repo)["proposals"]
    finally:
        reopened.close()


def test_keyword_recall_and_immutable_correction(graph):
    from visp_memory.core.storage import SemanticMemoryImmutableError

    s, repo = graph
    note = s.store_memory(
        "Authentication cache expires in five minutes", repo_id=repo, auto_link=False
    )
    results = s.search_memories("cache minutes", repo_id=repo)
    assert results[0]["id"] == note
    assert results[0]["similarity"] > 0
    assert results[0]["retrieval_method"] == "keyword"
    assert s.search_memories("cache", repo_id=repo + "-other") == []
    belief = s.store_memory(
        "Cache TTL is five minutes",
        repo_id=repo,
        layer="semantic",
        source_ids=[note],
        auto_link=False,
    )
    with pytest.raises(SemanticMemoryImmutableError):
        s.update_memory(belief, content="Rewritten without evidence")
    successor = s.revise_memory(
        belief,
        "TTL is five minutes for authentication",
        evidence_ids=[s.store_evidence("Authentication TTL verified by a new check", repo)],
    )
    assert s.get_memory(successor)["layer"] == "semantic"
    assert s.get_memory(belief)["status"] == "superseded"


def test_dreaming_rolls_back_partial_actions(graph, monkeypatch):
    from visp_memory.core.dreaming import journal

    s, repo = graph
    ids = [s.store_memory("Exact duplicate", repo_id=repo, auto_link=False) for _ in range(2)]
    original = journal.apply

    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("Injected interruption after journaling")

    monkeypatch.setattr(journal, "apply", fail)
    dream = Dreaming(s)
    with pytest.raises(RuntimeError, match="Injected"):
        dream.run(repo, actor_id="owner")
    assert all(s.get_memory(mid)["status"] == "active" for mid in ids)
    assert dream.history(repo) == []


def test_prohibition_requires_verified_authority_and_rejects_replay(graph, monkeypatch):
    import base64
    import json

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from visp_memory.core.authority import (
        PROHIBITION_AUTHORITY_KEYS_ENV,
        ProhibitionAuthorityError,
        build_prohibition_claim,
        sign_prohibition_attestation,
    )

    s, repo = graph
    key = Ed25519PrivateKey.generate()

    def encode(value):
        return base64.b64encode(value).decode("ascii")

    monkeypatch.setenv(
        PROHIBITION_AUTHORITY_KEYS_ENV,
        json.dumps(
            {
                "test-owner": encode(
                    key.public_key().public_bytes(
                        serialization.Encoding.Raw, serialization.PublicFormat.Raw
                    )
                )
            }
        ),
    )
    content = "Never disable signature checks"
    eid = s.store_evidence(content, repo)
    evidence = s.get_evidence(eid)
    arguments = dict(
        content=content,
        layer="semantic",
        category="prohibition",
        repo_id=repo,
        evidence_ids=[eid],
        auto_link=False,
        memory_id=uuid.uuid4().hex,
    )
    with pytest.raises(ProhibitionAuthorityError):
        s.store_memory(**arguments)
    claim = build_prohibition_claim(
        content=content,
        repo_id=repo,
        evidence=[{"id": eid, "content_hash": evidence["content_hash"]}],
    )
    envelope = sign_prohibition_attestation(
        claim,
        key_id="test-owner",
        private_key=encode(
            key.private_bytes(
                serialization.Encoding.Raw,
                serialization.PrivateFormat.Raw,
                serialization.NoEncryption(),
            )
        ),
        nonce=uuid.uuid4().hex,
        issued_at=utc_now(),
    )
    mid = s.store_memory(**arguments, authority_attestation=envelope)
    assert s.store_memory(**arguments, authority_attestation=envelope) == mid
    assert s.get_authority_attestation(mid)["envelope"] == envelope
    with pytest.raises(ProhibitionAuthorityError, match="replay"):
        s.store_memory(
            **{**arguments, "memory_id": uuid.uuid4().hex}, authority_attestation=envelope
        )


@pytest.fixture
def admin_driver():
    """Explicitly opted-in DISPOSABLE database; admin tests erase it between cases."""
    uri = os.environ.get("VISP_TEST_NEO4J_ADMIN_URI")
    if not uri:
        pytest.skip("Set VISP_TEST_NEO4J_ADMIN_URI to a separate disposable database")
    from neo4j import GraphDatabase

    with GraphDatabase.driver(
        uri, auth=("neo4j", os.environ["VISP_TEST_NEO4J_PASSWORD"])
    ) as driver:
        with driver.session() as session:
            session.run("MATCH (n) DETACH DELETE n").consume()
        yield driver
        with driver.session() as session:
            session.run("MATCH (n) DETACH DELETE n").consume()


def test_legacy_migration_backup_restore_preserves_history(admin_driver, tmp_path):
    from visp_memory.core.neo4j_admin import backup_graph, migrate_graph, restore_graph

    driver = admin_driver
    with driver.session() as session:
        session.run(
            "CREATE (:SchemaVersion {component: 'storage', version: 5}), "
            "(:Memory {id: 'legacy', repo_id: 'legacy-project', layer: 'semantic', "
            "content: 'Never skip the review', category: 'prohibition', status: 'active', "
            "metadata: '{}', created_at: datetime('2026-01-01T00:00:00Z')}), "
            "(:Intent {id: 'task', repo_id: 'legacy-project', status: 'completed', "
            'context: \'{"workflow_history":[{"event_id":"external"}]}\'})'
        ).consume()
    legacy_backup = tmp_path / "legacy.json"
    assert migrate_graph(driver, legacy_backup)["migrated_memories"] == 1
    assert legacy_backup.stat().st_mode & 0o777 == 0o600
    storage = Neo4jStorage(
        uri=os.environ["VISP_TEST_NEO4J_ADMIN_URI"],
        user="neo4j",
        password=os.environ["VISP_TEST_NEO4J_PASSWORD"],
    )
    try:
        memory = storage.get_memory("legacy")
        assert memory["belief_type"] == "hypothesis"
        assert memory["source"] == "unknown"
        assert storage.get_evidence(memory["evidence_ids"][0])["evidence_type"] == "legacy_snapshot"
        assert storage.get_active_intents("legacy-project", "all")[0]["status"] == "completed"
    finally:
        storage.close()
    backup = tmp_path / "current.json"
    before = backup_graph(driver, backup)
    with pytest.raises(ValueError, match="empty database"):
        restore_graph(driver, backup)
    with driver.session() as session:
        session.run("MATCH (n) DETACH DELETE n").consume()
    assert restore_graph(driver, backup) == before
    roundtrip = tmp_path / "restored.json"
    backup_graph(driver, roundtrip)
    import json

    def nodes(path):
        return sorted((n["labels"], n["properties"]) for n in json.loads(path.read_text())["nodes"])

    assert nodes(roundtrip) == nodes(backup)


def test_failed_legacy_migration_keeps_original_graph_and_backup(admin_driver, tmp_path):
    from visp_memory.core.neo4j_admin import migrate_graph

    with admin_driver.session() as session:
        session.run(
            "CREATE (:SchemaVersion {component: 'storage', version: 5}), "
            "(:Memory {id: 'broken', content: 'Preserve me', layer: 'episodic', "
            "repo_id: 'legacy', source_ids: ['missing']})"
        ).consume()
    path = tmp_path / "before-failure.json"
    with pytest.raises(EvidenceReferenceError):
        migrate_graph(admin_driver, path)
    assert path.exists()
    with admin_driver.session() as session:
        assert session.run("MATCH (e:Evidence) RETURN count(e) AS n").single()["n"] == 0
        assert (
            session.run("MATCH (m:Memory) RETURN m.content AS text").single()["text"]
            == "Preserve me"
        )


@pytest.mark.asyncio
async def test_http_capture_and_cited_belief_on_neo4j(graph, client, monkeypatch):
    from visp_memory.server.app import app

    storage, repo = graph
    monkeypatch.setattr(app.state, "storage", storage)
    headers = {"X-API-KEY": "test_key"}
    response = await client.post(
        "/memories", headers=headers, json={"repo_id": repo, "content": "HTTP captured observation"}
    )
    assert response.status_code == 200, response.text
    memory = response.json()
    evidence_ids = storage.get_memory(memory["id"])["evidence_ids"]
    response = await client.post(
        "/memories",
        headers=headers,
        json={
            "repo_id": repo,
            "content": "HTTP cited belief",
            "layer": "semantic",
            "category": "fact",
            "evidence_ids": evidence_ids,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["evidence_ids"] == evidence_ids
    response = await client.post(
        "/memories",
        headers=headers,
        json={
            "repo_id": repo + "-other",
            "content": "Cross-project citation",
            "layer": "semantic",
            "category": "fact",
            "evidence_ids": evidence_ids,
        },
    )
    assert response.status_code == 409


def test_native_vector_index(graph):
    original, repo = graph
    vector = Neo4jStorage(
        uri=original.uri,
        user=original.user,
        password=original.password,
        embedding_dimension=3,
        embedding_fn=lambda _: [0.8, 0.1, 0.1],
    )
    try:
        with vector.driver.session() as session:
            session.run("CALL db.awaitIndexes(30)").consume()
        mid = vector.store_memory("Native indexed observation", repo_id=repo, auto_link=False)
        results = vector.search_memories("Different query words", repo_id=repo)
        assert results[0]["id"] == mid
        assert results[0]["retrieval_method"] == "vector"
        assert results[0]["similarity"] > 0.9
    finally:
        vector.close()


def test_reopen_rejects_corrupt_citation_scope(graph):
    from visp_memory.core.storage import StorageMigrationRequired

    s, repo = graph
    mid = s.store_memory("Scope-checked capture", repo_id=repo, auto_link=False)
    eid = s.get_memory(mid)["evidence_ids"][0]
    with s.driver.session() as session:
        session.run(
            "MATCH (e:Evidence {id: $id}) SET e.repo_id = $repo", id=eid, repo=repo + "-other"
        ).consume()
    with pytest.raises(StorageMigrationRequired, match="scope"):
        Neo4jStorage(uri=s.uri, user=s.user, password=s.password)
