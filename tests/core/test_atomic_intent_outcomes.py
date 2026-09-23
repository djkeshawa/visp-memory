"""Concurrency regression tests for append-only intent outcome history."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from visp_memory.core.storage import LocalStorage
from visp_memory.core.trust import WriteChannel
from visp_memory.layers.intent import IntentMemory


def test_concurrent_intent_outcomes_preserve_every_entry(tmp_path):
    storage = LocalStorage(tmp_path)
    intents = IntentMemory(storage)
    intent_id = intents.set_goal("Exercise concurrent outcome writers", repo_id="repo-a")
    writer_count = 16
    barrier = Barrier(writer_count)

    def append(index: int) -> bool:
        barrier.wait()
        return intents.record_outcome(
            intent_id,
            f"writer-{index}",
            actor_id=f"actor-{index}",
            channel=WriteChannel.TEST_CAPTURE,
        )

    with ThreadPoolExecutor(max_workers=writer_count) as executor:
        results = list(executor.map(append, range(writer_count)))

    assert all(results)
    stored = next(
        item
        for item in storage.get_active_intents(repo_id="repo-a", status="all")
        if item["id"] == intent_id
    )
    history = stored["context"]["outcome_history"]
    assert len(history) == writer_count
    assert {entry["outcome"] for entry in history} == {
        f"writer-{index}" for index in range(writer_count)
    }
    assert all(entry["authoritative"] is False for entry in history)


def test_missing_intent_append_is_false_without_creating_state(tmp_path):
    storage = LocalStorage(tmp_path)

    assert storage.append_intent_outcome("missing", {"outcome": "completed"}) is False
    assert storage.get_active_intents(repo_id=None, status="all") == []


def test_real_arcadedb_driver_appends_an_outcome(tmp_path):
    pytest.importorskip("arcadedb_embedded")
    from visp_memory.core.arcadedb_storage import ArcadeDbStorage

    storage = ArcadeDbStorage(tmp_path)
    intents = IntentMemory(storage)
    intent_id = intents.set_goal("Persist an ArcadeDB outcome", repo_id="repo-a")

    assert intents.record_outcome(
        intent_id,
        "completed",
        actor_id="arcade-test",
        channel=WriteChannel.TEST_CAPTURE,
    )

    stored = next(
        item
        for item in storage.get_active_intents(repo_id="repo-a", status="all")
        if item["id"] == intent_id
    )
    assert [entry["outcome"] for entry in stored["context"]["outcome_history"]] == [
        "completed"
    ]


def test_real_arcadedb_driver_preserves_concurrent_outcomes(tmp_path):
    pytest.importorskip("arcadedb_embedded")
    from visp_memory.core.arcadedb_storage import ArcadeDbStorage

    storage = ArcadeDbStorage(tmp_path)
    intents = IntentMemory(storage)
    intent_id = intents.set_goal("Persist concurrent ArcadeDB outcomes", repo_id="repo-a")
    barrier = Barrier(2)

    def append(index: int) -> bool:
        barrier.wait()
        return intents.record_outcome(
            intent_id,
            f"writer-{index}",
            actor_id=f"arcade-{index}",
            channel=WriteChannel.TEST_CAPTURE,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(append, range(2)))

    assert all(results)
    stored = next(
        item
        for item in storage.get_active_intents(repo_id="repo-a", status="all")
        if item["id"] == intent_id
    )
    assert {entry["outcome"] for entry in stored["context"]["outcome_history"]} == {
        "writer-0",
        "writer-1",
    }
