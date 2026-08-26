"""Tests for write-time reconciliation (ADD/UPDATE/NOOP) and supersession."""

import tempfile
from pathlib import Path

import pytest

from visp_memory import Memory, MemoryConfig
from visp_memory.quality.conflict import ConflictVerdict
from visp_memory.quality.reconcile import Reconciler, content_overlap


@pytest.fixture
def memory():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        config = MemoryConfig()
        config.storage.data_dir = Path(tmpdir)
        config.embedding.provider = "noop"
        yield Memory(config=config)


def _semantic_count(memory: Memory) -> int:
    return len(memory._storage.list_memories(layer="semantic", status="active", limit=1000))


class TestContentOverlap:
    def test_identical_is_one(self):
        assert content_overlap("Use JWT tokens", "use jwt  tokens") == 1.0

    def test_disjoint_is_zero(self):
        assert content_overlap("database migration", "frontend styling") == 0.0

    def test_partial_between(self):
        score = content_overlap(
            "always pin container image digests",
            "always pin container image digests in ci pipelines",
        )
        assert 0.0 < score < 1.0


class TestReconcileDecisions:
    def test_exact_duplicate_is_noop(self, memory):
        first = memory.learn("Always use prepared statements for SQL", category="fact")
        second = memory.learn("Always use prepared statements for SQL", category="fact")

        assert second == first
        assert _semantic_count(memory) == 1

    def test_noop_reinforces_importance(self, memory):
        first = memory.learn("Deploys must go through staging", importance=0.5)
        memory.learn("Deploys must go through staging", importance=0.9)

        row = memory._storage.get_memory(first)
        assert row["importance"] == pytest.approx(0.9)

    def test_more_detailed_restatement_creates_successor(self, memory):
        first = memory.learn(
            "Pin container image digests in the deploy pipeline", category="preference"
        )
        second = memory.learn(
            "Pin container image digests in the deploy pipeline and verify them "
            "against the registry manifest",
            category="preference",
        )

        assert second != first
        successor = memory._storage.get_memory(second)
        assert "registry manifest" in successor["content"]
        assert successor["metadata"]["reconcile_action"] == "update"
        old = memory._storage.get_memory(first)
        assert old["status"] == "superseded"
        assert old["metadata"]["superseded_by"] == second
        assert _semantic_count(memory) == 1

    def test_unrelated_knowledge_adds_new_memory(self, memory):
        first = memory.learn("Use JWT tokens for API auth")
        second = memory.learn("The dashboard uses Next.js with app router")

        assert second != first
        assert _semantic_count(memory) == 2

    def test_cross_category_similarity_still_adds(self, memory):
        first = memory.learn("Retry uploads three times before failing", category="preference")
        second = memory.learn(
            "Retry uploads three times before failing", category="negative"
        )
        assert second != first
        assert _semantic_count(memory) == 2

    def test_reconcile_flag_bypasses(self, memory):
        first = memory.learn("Cache keys include the tenant id")
        second = memory.learn("Cache keys include the tenant id", reconcile=False)

        assert second != first
        assert _semantic_count(memory) == 2

    def test_config_disables_reconciliation(self, memory):
        memory.config.quality.write_reconciliation = False
        first = memory.learn("Feature flags default to off")
        second = memory.learn("Feature flags default to off")
        assert second != first


class TestSupersession:
    def test_confirmed_conflict_supersedes_old_memory(self, memory, monkeypatch):
        old_id = memory.learn("The API rate limit is 100 requests per minute")

        def fake_conflict(content, layer="semantic", repo_id=None):
            return ConflictVerdict.found(
                {
                    "conflict": True,
                    "reason": "Rate limit changed",
                    "conflicting_ids": [old_id],
                }
            )

        monkeypatch.setattr(memory, "check_conflict", fake_conflict)
        new_id = memory.learn("The API rate limit is 500 requests per minute")

        old_row = memory._storage.get_memory(old_id)
        assert old_row["status"] == "superseded"
        assert old_row["metadata"]["superseded_by"] == new_id
        assert old_row["metadata"]["invalid_at"]

        # Superseded knowledge leaves the active recall set...
        active_ids = {
            row["id"]
            for row in memory._storage.list_memories(layer="semantic", status="active", limit=100)
        }
        assert old_id not in active_ids
        assert new_id in active_ids
        # ...but stays auditable.
        superseded = memory._storage.list_memories(
            layer="semantic", status="superseded", limit=100
        )
        assert any(row["id"] == old_id for row in superseded)

    def test_auto_supersede_can_be_disabled(self, memory, monkeypatch):
        memory.config.quality.auto_supersede = False
        old_id = memory.learn("Sessions expire after 30 minutes")
        monkeypatch.setattr(
            memory,
            "check_conflict",
            lambda content, layer="semantic", repo_id=None: ConflictVerdict.found(
                {
                    "conflict": True,
                    "reason": "changed",
                    "conflicting_ids": [old_id],
                }
            ),
        )
        memory.learn("Sessions expire after 8 hours")

        old_row = memory._storage.get_memory(old_id)
        assert old_row["status"] == "active"


class TestReconcilerUnit:
    def test_decide_add_on_empty_store(self, memory):
        decision = Reconciler(memory._storage).decide("brand new fact", layer="semantic")
        assert decision.action == "add"

    def test_candidate_lookup_failure_is_fail_open(self):
        class BrokenStorage:
            def search_memories(self, **kwargs):
                raise RuntimeError("backend down")

        decision = Reconciler(BrokenStorage()).decide("anything", layer="semantic")
        assert decision.action == "add"
