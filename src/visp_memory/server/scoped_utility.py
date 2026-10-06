"""Aggregate recall utility only from records the caller can access."""

from collections import Counter
from heapq import nlargest
from itertools import chain

from visp_memory.core.storage import RECALL_EVENT_WEIGHTS
from visp_memory.server.authorization import has_admin_privileges
from visp_memory.server.memory_reads import iter_visible_memories


def _event_type(value):
    if value is None:
        return value
    normalized = str(value).strip().lower().replace("-", "_")
    if normalized not in RECALL_EVENT_WEIGHTS:
        raise ValueError(f"Invalid recall event type: {value}")
    return normalized


def _unrestricted(user, memory_id):
    return memory_id is not None or has_admin_privileges(user) or user.is_local_owner


def _visible_ids(storage, user, repo_id):
    return (
        memory["id"] for memory in iter_visible_memories(
            storage, user=user, repo_id=repo_id, status="all"
        )
    )


def _verification(reports):
    result = {"valid": True, "checked_events": 0, "cross_repository_events": 0, "violations": []}
    for report in reports:
        result["valid"] = result["valid"] and report["valid"]
        result["checked_events"] += report["checked_events"]
        result["cross_repository_events"] += report["cross_repository_events"]
        result["violations"].extend(report["violations"])
    return result


def inspect_visible_utility(
    storage, user, *, repo_id, memory_id=None, event_type=None, limit=50
):
    """ID-specific calls must already be authorized by the entry point."""
    event_type = _event_type(event_type)
    limit = max(0, int(limit))
    if _unrestricted(user, memory_id):
        return storage.inspect_recall_utility(
            repo_id=repo_id, memory_id=memory_id, event_type=event_type, limit=limit
        )
    by_event_type = Counter()
    signals, events, verification = [], [], []
    for visible_id in _visible_ids(storage, user, repo_id):
        report = storage.inspect_recall_utility(
            memory_id=visible_id, repo_id=repo_id, event_type=event_type, limit=limit
        )
        by_event_type.update(report["summary"]["by_event_type"])
        signals.extend(report["signals"])
        events = nlargest(
            limit, chain(events, report["events"]),
            key=lambda item: (item.get("created_at") or "", item["id"]),
        )
        verification.append(report["verification"])
    signals.sort(
        key=lambda item: (item.get("utility_score", 0.0), item.get("last_event_at") or ""),
        reverse=True,
    )
    return {
        "summary": {
            "total_events": sum(by_event_type.values()),
            "by_event_type": dict(by_event_type),
            "memories": len(signals),
        },
        "signals": signals,
        "events": events,
        "verification": _verification(verification),
    }


def verify_visible_utility(storage, user, *, repo_id, memory_id=None, event_type=None):
    """Verify visible records without including hidden IDs in diagnostics."""
    event_type = _event_type(event_type)
    if _unrestricted(user, memory_id):
        return storage.verify_recall_utility(
            repo_id=repo_id, memory_id=memory_id, event_type=event_type
        )
    return _verification(
        storage.verify_recall_utility(
            memory_id=visible_id, repo_id=repo_id, event_type=event_type
        )
        for visible_id in _visible_ids(storage, user, repo_id)
    )


def reset_visible_utility(storage, user, *, repo_id, memory_id=None, event_type=None):
    """Reset only the caller's visible records when the request names a project."""
    event_type = _event_type(event_type)
    if _unrestricted(user, memory_id):
        return storage.reset_recall_utility(
            repo_id=repo_id, memory_id=memory_id, event_type=event_type
        )
    return sum(
        storage.reset_recall_utility(
            memory_id=visible_id, repo_id=repo_id, event_type=event_type
        )
        for visible_id in _visible_ids(storage, user, repo_id)
    )
