"""Atomic, reversible status changes inside the memory database."""

import json
import secrets

from visp_memory.core.clock import utc_now_iso
from visp_memory.core.dreaming.planner import fingerprint

SCHEMA = """
CREATE TABLE IF NOT EXISTS dream_projects (
    repo_id TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 0,
    interval_hours INTEGER NOT NULL DEFAULT 24, next_run TEXT, cursor TEXT NOT NULL DEFAULT '',
    last_error TEXT
);
CREATE TABLE IF NOT EXISTS dream_runs (
    id TEXT PRIMARY KEY, repo_id TEXT NOT NULL, created_at TEXT NOT NULL, report TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS dream_runs_repo ON dream_runs(repo_id, created_at);
CREATE TABLE IF NOT EXISTS dream_actions (
    id TEXT PRIMARY KEY, run_id TEXT NOT NULL, repo_id TEXT NOT NULL,
    proposal_id TEXT NOT NULL, signature TEXT NOT NULL, kind TEXT NOT NULL,
    actor_id TEXT NOT NULL, created_at TEXT NOT NULL, status TEXT NOT NULL,
    snapshot TEXT NOT NULL, undone_at TEXT
);
CREATE TABLE IF NOT EXISTS dream_dismissals (
    repo_id TEXT NOT NULL, proposal_id TEXT NOT NULL, signature TEXT NOT NULL,
    PRIMARY KEY(repo_id, proposal_id, signature)
);
"""


def signature(item):
    return json.dumps(item["fingerprints"], sort_keys=True)


def apply(unit, run_id, repo_id, item, actor_id):
    """Caller holds a storage transaction and has checked the proposal policy."""
    memories = [unit.memory(mid) for mid in item["memory_ids"]]
    if any(
        not m
        or m["repo_id"] != repo_id
        or m["status"] != "active"
        or fingerprint(m) != item["fingerprints"][m["id"]]
        for m in memories
    ):
        raise ValueError("A source changed. Run dreaming again before reviewing it.")
    action_id = "drma_" + secrets.token_hex(10)
    changed = memories[1:] if item["kind"] == "duplicate" else memories
    snapshot = {}
    for memory in changed:
        metadata = {**memory["metadata"], "dreaming_action": action_id}
        state = "archived"
        if item["kind"] == "duplicate":
            metadata["merged_into"] = memories[0]["id"]
            state = "merged"
        before = {key: memory.get(key) for key in ("status", "metadata", "archived_at")}
        after = {
            "status": state,
            "metadata": metadata,
            "archived_at": utc_now_iso() if state == "archived" else memory.get("archived_at"),
        }
        unit.change_memory(memory["id"], after)
        snapshot[memory["id"]] = {
            "before": before,
            "after": after,
            "fingerprint": fingerprint(unit.memory(memory["id"])),
        }
    unit.put(
        "actions",
        dict(
            id=action_id,
            run_id=run_id,
            repo_id=repo_id,
            proposal_id=item["id"],
            signature=signature(item),
            kind=item["kind"],
            actor_id=actor_id,
            created_at=utc_now_iso(),
            status="applied",
            snapshot=json.dumps(snapshot),
            undone_at=None,
        ),
    )
    return action_id


def undo(unit, action, actor_id):
    if action["status"] != "applied":
        raise ValueError("This change has already been undone")
    snapshot = json.loads(action["snapshot"])
    for memory_id, saved in snapshot.items():
        current = unit.memory(memory_id)
        if not current or fingerprint(current) != saved["fingerprint"]:
            raise ValueError("A changed or deleted source prevents undo; review it in Memories.")
    for memory_id, saved in snapshot.items():
        before = saved["before"]
        unit.change_memory(memory_id, before)
    unit.put(
        "actions", {**action, "status": "undone", "undone_at": utc_now_iso(), "actor_id": actor_id}
    )
    unit.put(
        "dismissals",
        dict(
            repo_id=action["repo_id"],
            proposal_id=action["proposal_id"],
            signature=action["signature"],
        ),
    )


def linked_ids(conn, memory_ids):
    """Find protected graph/lineage references with a single scan of source lists."""
    if not memory_ids:
        return set()
    encoded = json.dumps(memory_ids)
    linked = set()
    for row in conn.execute(
        """SELECT source_id, target_id FROM relationships
        WHERE source_id IN (SELECT value FROM json_each(?))
        OR target_id IN (SELECT value FROM json_each(?))""",
        (encoded, encoded),
    ):
        linked.update((row["source_id"], row["target_id"]))
    linked.update(
        row[0]
        for row in conn.execute(
            """
        SELECT DISTINCT refs.value FROM memories, json_each(memories.source_ids) refs
        WHERE refs.value IN (SELECT value FROM json_each(?))""",
            (encoded,),
        )
    )
    return linked
