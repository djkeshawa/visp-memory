"""Tests for the token-efficiency accounting surfaces."""

import tempfile
from pathlib import Path

import pytest

from visp_memory import Memory, MemoryConfig
from visp_memory.core.trust import Provenance, provenance_of


@pytest.fixture
def memory():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        config = MemoryConfig(repo_id="repo-a")
        config.storage.data_dir = Path(tmpdir)
        config.embedding.provider = "noop"
        yield Memory(config=config)


def test_token_efficiency_empty_store_is_zeroed(memory):
    report = memory.token_efficiency()

    assert report["consolidation"]["consolidations"] == 0
    assert report["consolidation"]["saved_tokens"] == 0
    assert report["consolidation"]["ratio"] == 0.0
    assert report["total_saved_tokens"] >= 0


def test_compression_records_auditable_token_savings(memory):
    episodes = [
        memory._storage.get_memory(
            memory.record(
                f"Auth flow bug #{i}: race condition under concurrent refresh causing "
                f"intermittent 401s in the token endpoint",
                category="bug_fixed",
                importance=0.6,
            )
        )
        for i in range(4)
    ]

    semantic_id = memory._compressor.compress_episodes_to_semantic(episodes)
    assert semantic_id is not None

    semantic = memory._storage.get_memory(semantic_id)
    savings = semantic["metadata"]["token_savings"]
    assert semantic["metadata"]["write_channel"] == "compression"
    assert provenance_of(semantic) is Provenance.ASSISTED

    # Source material (4 detailed episodes) should cost more than the compact result.
    assert savings["source_tokens"] > savings["result_tokens"]
    assert savings["saved_tokens"] == savings["source_tokens"] - savings["result_tokens"]
    assert 0.0 < savings["ratio"] <= 1.0


def test_token_efficiency_aggregates_consolidation_savings(memory):
    episodes = [
        memory._storage.get_memory(
            memory.record(
                f"Deployment incident {i}: cache stampede after config reload took down "
                f"the pricing service for several minutes",
                category="incident",
                importance=0.7,
            )
        )
        for i in range(5)
    ]
    memory._compressor.compress_episodes_to_semantic(episodes)

    report = memory.token_efficiency()

    assert report["consolidation"]["consolidations"] == 1
    assert report["consolidation"]["saved_tokens"] > 0
    assert report["total_saved_tokens"] >= report["consolidation"]["saved_tokens"]


def test_token_efficiency_context_is_more_compact_than_full_store(memory):
    for i in range(8):
        memory.record(
            f"Investigated flaky integration test {i} and documented the root cause "
            f"in detail with reproduction steps and environment notes",
            category="investigation",
            importance=0.4,
        )
    memory.learn("Always pin test container image digests", category="preference")

    report = memory.token_efficiency()

    # The injected context should never be larger than the full active store.
    assert report["context"]["context_tokens"] <= report["context"]["full_store_tokens"]
    assert 0.0 <= report["context"]["compactness_ratio"] <= 1.0
    # Context compactness is descriptive and must NOT inflate the saved figure.
    assert report["total_saved_tokens"] == report["consolidation"]["saved_tokens"]


def test_token_efficiency_context_is_scoped_to_requested_repo(memory):
    # The context block must reflect the *requested* repo, not the configured default,
    # otherwise compactness compares context from one repo against the store of another.
    memory.config.repo_id = "repo-a"
    memory.warn("auth.py", "fragile auth refresh path", repo_id="repo-a")
    memory.goal("Ship the auth rewrite", repo_id="repo-a")
    memory.record("worked on repo-a auth", repo_id="repo-a")

    rich = memory.token_efficiency(repo_id="repo-a")
    sparse = memory.token_efficiency(repo_id="repo-b")  # empty repo

    # repo-a's context carries its warnings/goals; repo-b's does not. If context ignored
    # the repo argument (the bug), these would be identical.
    assert rich["context"]["context_tokens"] > sparse["context"]["context_tokens"]


def test_token_efficiency_tolerates_malformed_savings_metadata(memory):
    # A semantic memory whose token_savings is malformed must not crash aggregation.
    good = memory._storage.get_memory(
        memory.learn("solid established convention", category="preference")
    )
    memory._storage.update_memory(
        good["id"], metadata={"token_savings": {"source_tokens": "oops", "result_tokens": None}}
    )
    memory._storage.update_memory(
        memory.learn("another fact", category="fact"),
        metadata={"token_savings": "not-a-dict"},
    )

    report = memory.token_efficiency()  # must not raise

    assert report["consolidation"]["saved_tokens"] >= 0
    assert report["total_saved_tokens"] >= 0
