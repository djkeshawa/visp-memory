"""Lifecycle, eligibility, and trust guards shared by graph reads."""

from typing import Any

from visp_memory.core.clock import parse_utc
from visp_memory.core.eligibility import filter_recall_eligible
from visp_memory.core.storage import NON_SERVABLE_STATUSES
from visp_memory.core.trust import filter_unsolicited


def guard_graph_memories(
    memories: list[dict[str, Any]],
    repo_id: str,
    *,
    environment: Any = None,
    task_type: Any = None,
    as_of: Any = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    # Lifecycle first. Eligibility and trust both answer "should this be shown
    # to this caller, now" — neither asks whether the memory still exists. A
    # deleted, merged or superseded belief could pass both and be returned by
    # every graph read (MG-031), which is how deleting something failed to
    # make it go away.
    lifecycle_rejected = [
        memory
        for memory in memories
        if memory.get("status") in NON_SERVABLE_STATUSES
    ]
    memories = [
        memory
        for memory in memories
        if memory.get("status") not in NON_SERVABLE_STATUSES
    ]

    eligibility = filter_recall_eligible(
        memories,
        repo_id=repo_id,
        environment=environment,
        task_type=task_type,
        as_of=as_of,
    )
    trust = filter_unsolicited(
        eligibility.allowed,
        now=parse_utc(as_of) if as_of is not None else None,
    )
    omissions = [
        {
            "type": "lifecycle",
            "count": 1,
            "id": memory.get("id"),
            "status": memory.get("status"),
            "reason": f"memory is {memory.get('status')}",
        }
        for memory in lifecycle_rejected
    ]
    omissions.extend(
        {"type": "eligibility", "count": 1, **rejection.as_dict()}
        for rejection in eligibility.rejected
    )
    omissions.extend(
        {"type": "trust", "count": 1, **rejection.as_dict()}
        for rejection in trust.rejected
    )
    return trust.allowed, omissions

