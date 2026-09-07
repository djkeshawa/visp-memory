"""Refill recall candidates after authorization and eligibility rejections."""

from typing import Any, Callable

from visp_memory.core.clock import utc_now
from visp_memory.core.eligibility import EligibilityFilterResult, filter_recall_eligible


def recall_candidates(
    storage,
    query: str,
    *,
    repo_id: str,
    layers: list[str | None],
    limit: int,
    status: str = "active",
    environment: Any = None,
    task_type: Any = None,
    as_of: Any = None,
    memory_filter: Callable[[dict], bool] | None = None,
    rank_results: Callable[[list[dict]], list[dict]] | None = None,
) -> EligibilityFilterResult:
    scope = {
        "repo_id": repo_id,
        "environment": environment,
        "task_type": task_type,
        "as_of": utc_now() if as_of is None else as_of,
    }
    empty = filter_recall_eligible([], **scope)
    if limit <= 0:
        return empty
    return EligibilityFilterResult.combine(
        _layer_candidates(
            storage,
            query,
            layer=layer,
            limit=limit,
            status=status,
            scope=scope,
            memory_filter=memory_filter,
            rank_results=rank_results,
        )
        for layer in layers
    )


def _layer_candidates(
    storage,
    query,
    *,
    layer,
    limit,
    status,
    scope,
    memory_filter,
    rank_results,
) -> EligibilityFilterResult:
    candidate_limit = limit
    seen_ids = set()
    while True:
        candidates = storage.search_memories(
            query=query,
            layer=layer,
            limit=candidate_limit,
            status=status,
            min_score=0,
            **scope,
        )
        visible = [
            memory for memory in candidates if memory_filter is None or memory_filter(memory)
        ]
        eligibility = filter_recall_eligible(visible, **scope)
        usable = rank_results(eligibility.allowed) if rank_results else eligibility.allowed
        candidate_ids = {memory["id"] for memory in candidates}
        if (
            len(usable) >= limit
            or len(candidates) < candidate_limit
            or not candidate_ids.difference(seen_ids)
        ):
            return eligibility
        seen_ids.update(candidate_ids)
        # Search adapters have no shared offset contract. Geometric windows keep
        # the total candidate work linear in the deepest window, and stop when a
        # backend is exhausted or stops making progress (including capped adapters).
        candidate_limit *= 2
