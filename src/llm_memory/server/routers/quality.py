from collections import defaultdict
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, Request

from llm_memory.config import load_config
from llm_memory.server.auth import UserContext, get_current_user
from llm_memory.server.authorization import can_access_scoped_record, require_repo_scope_access
from llm_memory.server.schemas import (
    DecayPreviewItem,
    DecayPreviewResponse,
    DuplicateCandidate,
    QualityDuplicateResponse,
)

router = APIRouter(prefix="/quality", tags=["quality"])


def _normalize_content(value: str) -> str:
    return " ".join(value.casefold().split())


def _as_datetime(value):
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    return datetime.now()


def _decay_projection(
    memory: dict,
    *,
    now: datetime,
    halflife_days: int,
    min_importance: float,
) -> DecayPreviewItem:
    current_importance = float(memory.get("importance", 0.5) or 0.0)
    created_at = _as_datetime(memory.get("created_at"))
    last_accessed_at = _as_datetime(memory.get("accessed_at") or memory.get("created_at"))
    age_days = max(0.0, (now - last_accessed_at).total_seconds() / 86400)
    decay_factor = 0.5 ** (age_days / max(halflife_days, 1))
    projected_importance = max(min_importance, current_importance * decay_factor)
    decay_amount = max(0.0, current_importance - projected_importance)

    if current_importance <= min_importance + 0.001:
        risk = "at_floor"
        reason = "Already at or below the configured minimum importance."
    elif projected_importance <= min_importance + 0.05 or decay_amount >= 0.2:
        risk = "likely_to_decay"
        reason = "Long idle time would produce a material importance drop."
    elif decay_amount >= 0.05:
        risk = "weakening"
        reason = "Idle time would lower this memory's strength on the next decay pass."
    else:
        risk = "stable"
        reason = "Recent access and current importance keep this memory stable."

    content = memory.get("content") or ""
    return DecayPreviewItem(
        memory_id=memory["id"],
        snippet=content[:180],
        layer=memory.get("layer") or "episodic",
        category=memory.get("category"),
        repo_id=memory.get("repo_id"),
        current_importance=round(current_importance, 4),
        projected_importance=round(projected_importance, 4),
        decay_amount=round(decay_amount, 4),
        age_days=round(age_days, 2),
        access_count=int(memory.get("access_count") or 0),
        last_accessed_at=last_accessed_at,
        created_at=created_at,
        risk=risk,
        reason=reason,
    )


@router.get("/duplicates", response_model=QualityDuplicateResponse)
async def list_duplicate_candidates(
    request: Request,
    repo_id: str = None,
    layer: str = None,
    category: str = None,
    limit: int = 25,
    user: UserContext = Depends(get_current_user),
):
    """Return deterministic exact-content duplicate candidates for review."""
    storage = request.app.state.storage
    config = load_config()
    memory_repo_id = repo_id or config.repo_id
    require_repo_scope_access(storage, memory_repo_id, user)
    limit = max(1, min(limit, 100))

    memories = storage.list_memories(
        repo_id=memory_repo_id,
        layer=layer,
        category=category,
        status="active",
        limit=10000,
        order_by="created_at ASC",
    )
    visible_memories = [
        memory
        for memory in memories
        if can_access_scoped_record(storage, memory, user, scope_field="metadata")
    ]

    groups: dict[tuple[str, str, str | None, str], list[dict]] = defaultdict(list)
    for memory in visible_memories:
        normalized = _normalize_content(memory.get("content", ""))
        if not normalized:
            continue
        groups[
            (
                memory.get("repo_id") or "",
                memory.get("layer") or "",
                memory.get("category"),
                normalized,
            )
        ].append(memory)

    candidates: List[DuplicateCandidate] = []
    for (_repo_id, group_layer, group_category, _content), group in groups.items():
        if len(group) < 2:
            continue
        candidates.append(
            DuplicateCandidate(
                ids=[item["id"] for item in group],
                contents=[item["content"] for item in group],
                repo_id=group[0].get("repo_id"),
                layer=group_layer,
                category=group_category,
                similarity=1.0,
                reason="Exact normalized content match.",
            )
        )
        if len(candidates) >= limit:
            break

    return QualityDuplicateResponse(candidates=candidates)


@router.get("/decay-preview", response_model=DecayPreviewResponse)
async def list_decay_preview(
    request: Request,
    repo_id: str = None,
    layer: str = None,
    category: str = None,
    limit: int = 25,
    halflife_days: int = None,
    min_importance: float = 0.1,
    user: UserContext = Depends(get_current_user),
):
    """Preview which memories would lose strength during decay without mutating storage."""
    storage = request.app.state.storage
    config = load_config()
    memory_repo_id = repo_id or config.repo_id
    require_repo_scope_access(storage, memory_repo_id, user)
    limit = max(1, min(limit, 100))
    effective_halflife_days = max(1, halflife_days or config.decay_halflife_days)
    min_importance = max(0.0, min(min_importance, 1.0))

    memories = storage.list_memories(
        repo_id=memory_repo_id,
        layer=layer,
        category=category,
        status="active",
        limit=10000,
        order_by="accessed_at ASC",
    )
    visible_memories = [
        memory
        for memory in memories
        if can_access_scoped_record(storage, memory, user, scope_field="metadata")
    ]

    now = datetime.now()
    candidates = [
        _decay_projection(
            memory,
            now=now,
            halflife_days=effective_halflife_days,
            min_importance=min_importance,
        )
        for memory in visible_memories
    ]
    risk_rank = {"likely_to_decay": 0, "weakening": 1, "at_floor": 2, "stable": 3}
    candidates.sort(
        key=lambda item: (
            risk_rank[item.risk],
            -item.decay_amount,
            item.projected_importance,
            -item.age_days,
        )
    )

    return DecayPreviewResponse(
        halflife_days=effective_halflife_days,
        min_importance=min_importance,
        decay_enabled=config.decay_enabled,
        candidates=candidates[:limit],
    )
