"""Tests for retrieval-induced reinforcement ("use it or lose it").

Using a memory reinforces it: its access count grows (boosting future recall via
base-level activation) and its last-access time refreshes (resetting decay). Merely
surfacing a memory does not reinforce it, which avoids popularity bias from exposure.
"""

from datetime import datetime, timedelta, timezone

from visp_memory.core.compression import MemoryCompressor
from visp_memory.core.storage import LocalStorage


def _access_count(storage: LocalStorage, memory_id: str) -> int:
    # Read without tracking so the assertion does not perturb the counter.
    row = storage._get_memory_row(memory_id, track_access=False)
    return int(row["access_count"])


def test_used_event_reinforces_access_count(tmp_path):
    storage = LocalStorage(tmp_path)
    memory_id = storage.store_memory("auth refresh race condition", auto_link=False)
    assert _access_count(storage, memory_id) == 0

    storage.log_recall_event(memory_id, "used")
    assert _access_count(storage, memory_id) == 1

    storage.log_recall_event(memory_id, "task_linked")
    assert _access_count(storage, memory_id) == 2

    storage.log_recall_event(memory_id, "outcome_linked")
    assert _access_count(storage, memory_id) == 3


def test_surfaced_and_dismissed_events_do_not_reinforce(tmp_path):
    storage = LocalStorage(tmp_path)
    memory_id = storage.store_memory("auth refresh race condition", auto_link=False)

    storage.log_recall_event(memory_id, "surfaced")
    storage.log_recall_event(memory_id, "dismissed")

    assert _access_count(storage, memory_id) == 0


def test_use_refreshes_last_access_time_to_now(tmp_path):
    storage = LocalStorage(tmp_path)
    memory_id = storage.store_memory("auth refresh race condition", auto_link=False)

    storage.log_recall_event(memory_id, "used")

    # Reinforcement stamps accessed_at with SQLite CURRENT_TIMESTAMP (UTC). It must
    # land at "now" so decay (which measures idle time) treats the memory as active.
    raw = storage._get_memory_row(memory_id, track_access=False)["accessed_at"]
    parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00").replace(" ", "T"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    idle_seconds = (datetime.now(timezone.utc) - parsed).total_seconds()
    assert -5 <= idle_seconds <= 300


def _backdate_and_set_access(storage: LocalStorage, memory_id: str, days: int, access: int):
    old = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with storage._get_db() as conn:
        conn.execute(
            "UPDATE memories SET accessed_at = ?, access_count = ? WHERE id = ?",
            (old, access, memory_id),
        )
        conn.commit()


def test_decay_spares_frequently_used_memories(tmp_path):
    storage = LocalStorage(tmp_path)
    unused = storage.store_memory("rarely needed note", layer="semantic", auto_link=False)
    used = storage.store_memory("the deploy incantation", layer="semantic", auto_link=False)

    # Same age and starting importance, but the used memory has many recalls.
    storage.update_memory(unused, importance=0.8)
    storage.update_memory(used, importance=0.8)
    _backdate_and_set_access(storage, unused, days=90, access=0)
    _backdate_and_set_access(storage, used, days=90, access=25)

    MemoryCompressor(storage).decay_old_memories(halflife_days=30, min_importance=0.1)

    unused_after = storage._get_memory_row(unused, track_access=False)["importance"]
    used_after = storage._get_memory_row(used, track_access=False)["importance"]
    # Spaced repetition: the frequently-used memory retains more importance.
    assert used_after > unused_after


def test_decay_tolerates_null_accessed_at(tmp_path):
    storage = LocalStorage(tmp_path)
    memory_id = storage.store_memory("null access time note", layer="semantic", auto_link=False)
    old = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
    with storage._get_db() as conn:
        # accessed_at explicitly NULL must fall back to created_at, not crash.
        conn.execute(
            "UPDATE memories SET accessed_at = NULL, created_at = ? WHERE id = ?",
            (old, memory_id),
        )
        conn.commit()

    # Must not raise (regression guard for None accessed_at).
    MemoryCompressor(storage).decay_old_memories(halflife_days=30, min_importance=0.1)
