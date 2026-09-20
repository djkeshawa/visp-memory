"""Feedback must cooperate with retrieval and lifecycle on both supported stores."""

import hashlib
import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from visp_memory.core.neo4j_storage import Neo4jStorage
from visp_memory.core.ranking import UTILITY_RANKING_LIMIT, rank_memory_results
from visp_memory.core.storage import LocalStorage
from visp_memory.core.task_brief import TaskMemoryBriefCompiler


@pytest.fixture(params=["sqlite", "neo4j"])
def feedback_store(request, tmp_path):
    repo = "feedback-" + uuid.uuid4().hex
    if request.param == "sqlite":
        storage = LocalStorage(tmp_path)
    else:
        uri = os.environ.get("VISP_TEST_NEO4J_URI")
        if not uri:
            pytest.skip("Set VISP_TEST_NEO4J_URI for the dedicated integration database")
        storage = Neo4jStorage(uri=uri, password=os.environ["VISP_TEST_NEO4J_PASSWORD"])
    yield storage, repo
    if request.param == "neo4j":
        with storage.driver.session() as session:
            session.run(
                "MATCH (n) WHERE n.repo_id IN $repos OR "
                "(n:Repository AND n.id IN $repos) DETACH DELETE n",
                repos=[repo, repo + "-other"],
            ).consume()
    storage.close()


def record(storage, repo, content="OAuth refresh tokens retain family lineage.", **kwargs):
    return storage.store_memory(
        content, repo_id=repo, tags=["provenance:authored"], auto_link=False, **kwargs,
    )


def peek(storage, repo, mid):
    return next(row for row in storage.list_memories(repo_id=repo, status="all")
                if row["id"] == mid)


def test_feedback_privacy_aggregation_limits_reset_and_read_only_inspection(feedback_store):
    storage, repo = feedback_store
    mid = record(storage, repo)
    before = peek(storage, repo, mid)
    surfaced = storage.log_recall_event(
        mid, "surfaced", query="a private query", task_id="task-1",
        metadata={"source": "contract", "prompt": "omit", "response": "omit"},
    )
    storage.log_recall_event(mid, "dismissed")
    report = storage.inspect_recall_utility(memory_id=mid)
    assert report["summary"] == {
        "total_events": 2, "by_event_type": {"surfaced": 1, "dismissed": 1}, "memories": 1,
    }
    event = next(e for e in report["events"] if e["id"] == surfaced)
    assert event["repo_id"] == repo
    assert event["query_hash"] == hashlib.sha256(b"a private query").hexdigest()
    assert "query" not in event
    assert event["task_id"] == "task-1" and event["metadata"] == {"source": "contract"}
    assert report["signals"][0]["utility_score"] == pytest.approx(-0.22)
    assert storage.inspect_recall_utility(memory_id=mid, limit=0)["events"] == []
    assert storage.inspect_recall_utility(memory_id=mid, limit=1)["summary"] == report["summary"]
    assert storage.verify_recall_utility(memory_id=mid)["valid"]
    assert storage.reset_recall_utility(memory_id=mid, event_type="dismissed") == 1
    assert storage.reset_recall_utility(repo_id=repo) == 1
    after = peek(storage, repo, mid)
    assert (after["access_count"], after["accessed_at"]) == (
        before["access_count"], before["accessed_at"],
    )


@pytest.mark.parametrize("operation", [
    "log_recall_event", "inspect_recall_utility", "verify_recall_utility", "reset_recall_utility",
])
def test_feedback_rejects_foreign_scope_without_access(feedback_store, operation):
    storage, repo = feedback_store
    mid = record(storage, repo)
    before = peek(storage, repo, mid)
    method = getattr(storage, operation)
    args = {"event_type": "used"} if operation == "log_recall_event" else {}
    with pytest.raises(ValueError, match="repository mismatch"):
        method(memory_id=mid, repo_id=repo + "-other", **args)
    with pytest.raises(ValueError, match="Memory not found"):
        method(memory_id="missing", repo_id=repo, **args)
    assert storage.inspect_recall_utility(repo_id=repo)["summary"]["total_events"] == 0
    assert peek(storage, repo, mid)["access_count"] == before["access_count"]


def test_feedback_aliases_concurrent_reinforcement_and_invalid_events(feedback_store):
    storage, repo = feedback_store
    mid = record(storage, repo)
    with pytest.raises(ValueError, match="Invalid recall event type"):
        storage.log_recall_event(mid, "made-up-event")
    with pytest.raises(ValueError, match="Memory not found"):
        storage.log_recall_event(None, "used")
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(lambda _: storage.log_recall_event(mid, "used"), range(12)))
    assert len(set(ids)) == 12
    storage.log_recall_event(mid, "task-linked", task_id="t")
    storage.log_recall_event(mid, "outcome-linked", outcome="tested")
    report = storage.inspect_recall_utility(memory_id=mid)
    assert report["summary"]["by_event_type"] == {
        "used": 12, "task_linked": 1, "outcome_linked": 1,
    }
    assert report["signals"][0]["utility_score"] == 1.0
    assert peek(storage, repo, mid)["access_count"] == 14
    assert storage.reset_recall_utility(memory_id=mid, event_type="task-linked") == 1


def inject_misattributed_event(storage, repo, mid):
    event = {
        "id": "legacy-" + uuid.uuid4().hex, "memory_id": mid, "repo_id": repo + "-other",
        "event_type": "dismissed", "created_at": "2024-01-01T00:00:00+00:00", "metadata": "{}",
    }
    if isinstance(storage, LocalStorage):
        with storage._get_db() as db:
            db.execute(
                "INSERT INTO recall_events "
                "(id, memory_id, repo_id, event_type, created_at, metadata) "
                "VALUES (:id, :memory_id, :repo_id, :event_type, :created_at, :metadata)", event,
            )
            db.commit()
    else:
        with storage.driver.session() as db:
            db.run("CREATE (e:RecallFeedback) SET e = $event", event=event).consume()
    return event["id"]


def test_feedback_filters_legacy_scope_but_keeps_history_diagnosable(feedback_store):
    storage, repo = feedback_store
    mid = record(storage, repo)
    good = storage.log_recall_event(mid, "used")
    bad = inject_misattributed_event(storage, repo, mid)
    report = storage.inspect_recall_utility(memory_id=mid)
    assert [event["id"] for event in report["events"]] == [good]
    assert report["verification"] == {
        "valid": False, "checked_events": 2, "cross_repository_events": 1,
        "violations": [{"event_id": bad, "memory_id": mid,
                        "event_repo_id": repo + "-other", "memory_repo_id": repo}],
    }
    assert storage.inspect_recall_utility(repo_id=repo)["summary"]["total_events"] == 1
    assert storage.inspect_recall_utility(repo_id=repo + "-other")["summary"]["total_events"] == 0
    assert storage.reset_recall_utility(repo_id=repo + "-other") == 0
    rows = storage.search_memories("OAuth refresh", repo_id=repo)
    assert rows[0]["utility_signal"]["counts"] == {"used": 1}
    assert storage.reset_recall_utility(memory_id=mid) == 1
    assert storage.verify_recall_utility(repo_id=repo)["violations"][0]["event_id"] == bad


def test_feedback_ranking_is_bounded_and_does_not_override_brief_eligibility(feedback_store):
    storage, repo = feedback_store
    current = record(storage, repo)
    stale = record(storage, repo, "OAuth refresh tokens never expire.", status="superseded")
    external = storage.store_memory(
        "OAuth refresh token logs require exposing credentials.", repo_id=repo,
        tags=["provenance:external"], auto_link=False,
    )
    for mid in (current, stale, external):
        for _ in range(4):
            storage.log_recall_event(mid, "used")
        if mid != current:
            storage.add_relationship(current, mid, "related_to")
    rows = storage.search_memories("OAuth refresh token", repo_id=repo)
    selected = next(row for row in rows if row["id"] == current)
    assert selected["utility_score"] == 1.0
    assert selected["utility_signal"]["rank_adjustment"] == UTILITY_RANKING_LIMIT
    neutral = {**selected, "utility_score": 0}
    ranked = rank_memory_results([selected], query="OAuth refresh token")[0]
    baseline = rank_memory_results([neutral], query="OAuth refresh token")[0]
    assert 0 < ranked["relevance_score"] - baseline["relevance_score"] <= (
        UTILITY_RANKING_LIMIT + 1e-6
    )
    brief = TaskMemoryBriefCompiler(storage).prepare(
        "Review OAuth refresh token", repo_id=repo, ranking_strategy="hybrid", token_budget=1000,
    )
    cited = {row["memory_id"] for row in brief["citations"]}
    assert current in cited and not {stale, external} & cited


def test_feedback_survives_reopen_and_is_removed_with_deleted_memory(feedback_store, tmp_path):
    storage, repo = feedback_store
    mid = record(storage, repo)
    storage.log_recall_event(mid, "used")
    reopened = (
        LocalStorage(tmp_path) if isinstance(storage, LocalStorage)
        else Neo4jStorage(uri=storage.uri, user=storage.user, password=storage.password)
    )
    try:
        assert reopened.inspect_recall_utility(memory_id=mid)["summary"]["total_events"] == 1
        assert reopened.delete_memory(mid)
        assert reopened.inspect_recall_utility(repo_id=repo)["summary"]["total_events"] == 0
        if isinstance(reopened, Neo4jStorage):
            with reopened.driver.session() as db:
                assert db.run(
                    "MATCH (e:RecallFeedback {memory_id: $mid}) RETURN count(e) AS c", mid=mid,
                ).single()["c"] == 0
    finally:
        reopened.close()


def test_feedback_purge_preserves_the_other_repository(feedback_store):
    storage, repo = feedback_store
    mid = record(storage, repo)
    other = record(storage, repo + "-other")
    storage.log_recall_event(mid, "used")
    storage.log_recall_event(other, "used")
    storage.purge_repository(repo)
    assert storage.inspect_recall_utility(repo_id=repo)["summary"]["total_events"] == 0
    assert storage.inspect_recall_utility(memory_id=other)["summary"]["total_events"] == 1


def test_neo4j_feedback_append_and_reinforcement_roll_back_together(feedback_store, monkeypatch):
    from contextlib import contextmanager

    storage, repo = feedback_store
    if not isinstance(storage, Neo4jStorage):
        pytest.skip("Neo4j transaction fault injection")
    mid = record(storage, repo)
    original = storage._write_session

    @contextmanager
    def interrupted_write():
        with original() as tx:
            yield tx
            raise RuntimeError("injected interruption before commit")

    monkeypatch.setattr(storage, "_write_session", interrupted_write)
    with pytest.raises(RuntimeError, match="injected interruption"):
        storage.log_recall_event(mid, "used")
    assert storage.inspect_recall_utility(memory_id=mid)["summary"]["total_events"] == 0
    assert peek(storage, repo, mid)["access_count"] == 0
