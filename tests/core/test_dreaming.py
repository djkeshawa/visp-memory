from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest

from visp_memory.core.clock import utc_now
from visp_memory.core.dreaming import Dreaming
from visp_memory.core.dreaming.scheduler import run_due
from visp_memory.core.storage import LocalStorage


@pytest.fixture
def dream(tmp_path):
    store = LocalStorage(tmp_path)
    store.store_memory("Initial project memory", repo_id="repo-a", auto_link=False)
    return Dreaming(store)


def duplicates(dream, **kwargs):
    return [
        dream.storage.store_memory(
            "Cache sessions for five minutes", repo_id="repo-a", auto_link=False, **kwargs
        )
        for _ in range(2)
    ]


def test_preview_is_read_only_and_run_merges_with_undo(dream):
    ids = duplicates(dream)
    preview = dream.preview("repo-a")
    assert preview["proposals"][0]["automatic"]
    assert all(dream.storage.get_memory(mid)["status"] == "active" for mid in ids)
    report = dream.run("repo-a", actor_id="owner")
    change = report["proposals"][0]
    assert sum(dream.storage.get_memory(mid)["status"] == "merged" for mid in ids) == 1
    assert dream.history("repo-a")[0]["proposals"][0]["resolution"] == "applied"
    dream.undo("repo-a", change["action_id"], actor_id="owner")
    assert all(dream.storage.get_memory(mid)["status"] == "active" for mid in ids)
    assert not dream.preview("repo-a")["proposals"]
    assert dream.history("repo-a")[0]["proposals"][0]["resolution"] == "undone"


@pytest.mark.parametrize("metadata", [{"pinned": True}, {"hold": True}])
def test_protected_notes_are_never_merged(dream, metadata):
    ids = duplicates(dream, metadata=metadata)
    report = dream.run("repo-a", actor_id="owner")
    assert not report["proposals"][0]["automatic"]
    assert all(dream.storage.get_memory(mid)["status"] == "active" for mid in ids)


def test_scope_case_and_graph_links_block_automatic_merge(dream):
    ids = duplicates(dream)
    dream.storage.add_relationship(ids[0], ids[1], "related")
    assert not dream.preview("repo-a")["proposals"][0]["automatic"]
    for content, metadata in [
        ("Use Token", {"team_id": "one"}),
        ("use token", {"team_id": "one"}),
        ("Use Token", {"team_id": "two"}),
    ]:
        dream.storage.store_memory(content, repo_id="repo-a", metadata=metadata, auto_link=False)
    assert len(dream.preview("repo-a")["proposals"]) == 1


def test_expired_memory_requires_review_and_changed_source_refuses(dream):
    mid = dream.storage.store_memory(
        "Old configuration",
        repo_id="repo-a",
        auto_link=False,
        metadata={"valid_to": (utc_now() - timedelta(days=1)).isoformat()},
    )
    report = dream.run("repo-a", actor_id="owner")
    item = next(p for p in report["proposals"] if p["kind"] == "expired")
    assert dream.storage.get_memory(mid)["status"] == "active"
    dream.storage.update_memory(mid, importance=0.9)
    with pytest.raises(ValueError, match="source changed"):
        dream.review("repo-a", report["id"], item["id"], "archive", actor_id="owner")
    fresh = dream.run("repo-a", actor_id="owner")
    item = next(p for p in fresh["proposals"] if p["kind"] == "expired")
    result = dream.review("repo-a", fresh["id"], item["id"], "archive", actor_id="owner")
    assert dream.storage.get_memory(mid)["status"] == "archived"
    dream.undo("repo-a", result["action_id"], actor_id="owner")
    assert dream.storage.get_memory(mid)["status"] == "active"


def test_undo_refuses_to_overwrite_subsequent_edits(dream):
    duplicates(dream)
    item = dream.run("repo-a", actor_id="owner")["proposals"][0]
    dream.storage.update_memory(item["memory_ids"][1], importance=0.8)
    with pytest.raises(ValueError, match="prevents undo"):
        dream.undo("repo-a", item["action_id"], actor_id="owner")


def test_due_run_is_once_across_workers_and_respects_disabled_schedule(dream):
    duplicates(dream)
    run_due(dream)
    assert dream.history("repo-a") == []
    dream.configure("repo-a", enabled=True)
    with dream.storage._get_db() as conn:
        conn.execute(
            "UPDATE dream_projects SET next_run = ?",
            ((utc_now() - timedelta(seconds=1)).isoformat(),),
        )
        conn.commit()
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: run_due(dream), range(2)))
    assert len(dream.history("repo-a")) == 1
    dream.configure("repo-a", enabled=False)
    assert dream.settings("repo-a")["next_run"] is None


def test_failed_cycle_rolls_back_actions_and_history(dream, monkeypatch):
    from visp_memory.core.dreaming import journal

    ids = duplicates(dream)
    real_apply = journal.apply

    def fail(*args, **kwargs):
        real_apply(*args, **kwargs)
        raise RuntimeError("interrupted")

    monkeypatch.setattr(journal, "apply", fail)
    with pytest.raises(RuntimeError):
        dream.run("repo-a", actor_id="owner")
    assert all(dream.storage.get_memory(mid)["status"] == "active" for mid in ids)
    assert dream.history("repo-a") == []


def test_related_and_conflicting_notes_remain_proposals(dream):
    for content in (
        "The login service caches session tokens securely",
        "The login service does not cache session tokens securely",
        "The login service caches session tokens securely for performance",
    ):
        dream.storage.store_memory(content, repo_id="repo-a", auto_link=False)
    result = dream.run("repo-a", actor_id="owner")
    assert {"related", "conflict"} <= {p["kind"] for p in result["proposals"]}
    assert all(p["resolution"] == "pending" for p in result["proposals"])
    assert not any(m["status"] != "active" for m in dream.storage.list_memories(repo_id="repo-a"))


def test_scheduler_records_failure_and_preserves_data(dream, monkeypatch):
    ids = duplicates(dream)
    dream.configure("repo-a", enabled=True)
    with dream.storage._get_db() as conn:
        conn.execute(
            "UPDATE dream_projects SET next_run = ?",
            ((utc_now() - timedelta(seconds=1)).isoformat(),),
        )
        conn.commit()

    def fail(*args, **kwargs):
        raise RuntimeError("backend unavailable")

    monkeypatch.setattr(dream, "run", fail)
    run_due(dream)
    assert dream.settings("repo-a")["last_error"]
    assert all(dream.storage.get_memory(mid)["status"] == "active" for mid in ids)


def test_batches_advance_and_return_to_beginning(dream, monkeypatch):
    import visp_memory.core.dreaming as module

    monkeypatch.setattr(module, "SCAN_LIMIT", 2)
    for index in range(4):
        dream.storage.store_memory(
            f"Independent observation number {index}", repo_id="repo-a", auto_link=False
        )
    seen = []
    for _ in range(3):
        seen.append(dream.run("repo-a", actor_id="owner")["scanned"])
    assert seen == [2, 2, 1]
    assert dream.settings("repo-a")["cursor"] == ""


@pytest.mark.asyncio
async def test_scheduler_waits_for_idle_and_stops_cleanly(dream, monkeypatch):
    import asyncio
    import time
    from types import SimpleNamespace

    from visp_memory.core.dreaming import scheduler

    calls = []
    monkeypatch.setattr(scheduler, "POLL_SECONDS", 0.005)
    monkeypatch.setattr(scheduler, "run_due", lambda service: calls.append(service))
    stop = asyncio.Event()
    app = SimpleNamespace(
        state=SimpleNamespace(dreaming=dream, dream_last_activity=time.monotonic())
    )
    task = asyncio.create_task(scheduler.dreaming_loop(app, stop))
    await asyncio.sleep(0.02)
    assert calls == []
    app.state.dream_last_activity -= 600
    await asyncio.sleep(0.03)
    stop.set()
    await task
    assert calls


def test_derived_sources_and_different_importance_are_not_merged(dream):
    ids = duplicates(dream)
    dream.storage.store_memory(
        "A derived note", repo_id="repo-a", auto_link=False, source_ids=[ids[0]]
    )
    assert not dream.preview("repo-a")["proposals"][0]["automatic"]
    dream.storage.update_memory(ids[1], importance=0.95)
    assert not any(p["kind"] == "duplicate" for p in dream.preview("repo-a")["proposals"])


def test_most_used_duplicate_is_retained(dream):
    ids = duplicates(dream)
    with dream.storage._get_db() as conn:
        conn.execute("UPDATE memories SET access_count = 20 WHERE id = ?", (ids[1],))
        conn.commit()
    dream.run("repo-a", actor_id="owner")
    assert dream.storage.get_memory(ids[1])["status"] == "active"
    assert dream.storage.get_memory(ids[0])["status"] == "merged"
