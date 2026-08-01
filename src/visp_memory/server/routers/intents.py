from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request

from visp_memory.config import load_config
from visp_memory.core.clock import utc_now
from visp_memory.core.trust import WriteChannel
from visp_memory.layers.intent import IntentMemory
from visp_memory.server.auth import UserContext, get_current_user
from visp_memory.server.authorization import (
    can_access_scoped_record,
    require_repo_scope_access,
    require_repo_writable,
    require_scoped_record_access,
)
from visp_memory.server.routers.platform import append_audit_event
from visp_memory.server.schemas import (
    IntentCreate,
    IntentEvaluationRequest,
    IntentResponse,
    IntentUpdate,
)

router = APIRouter(prefix="/intents", tags=["intents"])
STATUS_AUTHORITY_DETAIL = (
    "Visp Memory does not change intent status; use an external workflow authority"
)


def _as_datetime(value):
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    return value or utc_now()


def _intent_response_payload(intent: dict) -> dict:
    return {
        "id": intent["id"],
        "description": intent["description"],
        "priority": intent.get("priority", 0),
        "repo_id": intent.get("repo_id"),
        "context": intent.get("context", {}),
        "status": intent.get("status", "active"),
        "created_at": _as_datetime(intent.get("created_at")),
        "updated_at": _as_datetime(intent.get("updated_at")) if intent.get("updated_at") else None,
    }


def _find_intent(storage, intent_id: str):
    return next(
        (
            item
            for item in storage.get_active_intents(repo_id=None, status="all")
            if item["id"] == intent_id
        ),
        None,
    )


@router.get("", response_model=List[IntentResponse])
async def list_intents(
    request: Request,
    repo_id: str = None,
    status: str = "active",
    user: UserContext = Depends(get_current_user),
):
    storage = request.app.state.storage
    config = load_config()
    intent_repo_id = repo_id or config.repo_id
    require_repo_scope_access(storage, intent_repo_id, user)
    if status not in {"active", "completed", "closed", "all"}:
        raise HTTPException(status_code=422, detail="Invalid intent status")
    intents = storage.get_active_intents(repo_id=intent_repo_id, status=status)
    intents = [
        i for i in intents if can_access_scoped_record(storage, i, user, scope_field="context")
    ]

    return [_intent_response_payload(i) for i in intents]


@router.post("", response_model=IntentResponse)
async def create_intent(
    request: Request, intent: IntentCreate, user: UserContext = Depends(get_current_user)
):
    storage = request.app.state.storage
    config = load_config()
    intent_repo_id = intent.repo_id or config.repo_id
    require_repo_writable(storage, intent_repo_id, user)

    # Add author attribution
    context = dict(intent.context or {})
    context["author_id"] = user.user_id
    if user.team_id:
        context["team_id"] = user.team_id

    intent_id = storage.set_intent(
        description=intent.description,
        priority=intent.priority if hasattr(intent, "priority") else 0,
        repo_id=intent_repo_id,
        context=context,
    )

    return {
        "id": intent_id,
        "description": intent.description,
        "priority": getattr(intent, "priority", 0),
        "repo_id": intent_repo_id,
        "status": "active",
        "context": context,
        "created_at": utc_now(),
        "updated_at": utc_now(),
    }


@router.patch("/{intent_id}", response_model=IntentResponse)
async def update_intent(
    request: Request,
    intent_id: str,
    update: IntentUpdate,
    user: UserContext = Depends(get_current_user),
):
    storage = request.app.state.storage
    intent = _find_intent(storage, intent_id)
    intent = require_scoped_record_access(
        storage, intent, user, scope_field="context", not_found_detail="Intent not found"
    )

    update_data = {k: v for k, v in update.model_dump().items() if v is not None}
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")
    if "status" in update_data:
        raise HTTPException(status_code=409, detail=STATUS_AUTHORITY_DETAIL)

    if "context" in update_data and not user.is_admin:
        context = dict(update_data["context"] or {})
        existing_context = intent.get("context") or {}
        for reserved_key in ("author_id", "team_id"):
            if reserved_key in existing_context:
                context[reserved_key] = existing_context[reserved_key]
            else:
                context.pop(reserved_key, None)
        update_data["context"] = context

    success = storage.update_intent(intent_id, **update_data)
    if not success:
        raise HTTPException(status_code=404, detail="Intent not found")

    append_audit_event(
        storage,
        event_type="intent.updated",
        actor_id=user.user_id,
        repo_id=intent.get("repo_id"),
        target_type="intent",
        target_id=intent_id,
        metadata={"fields": sorted(update_data)},
    )
    refreshed = _find_intent(storage, intent_id)
    return _intent_response_payload(refreshed)


@router.post("/{intent_id}/complete")
async def complete_intent(
    request: Request, intent_id: str, user: UserContext = Depends(get_current_user)
):
    storage = request.app.state.storage
    intent = _find_intent(storage, intent_id)
    require_scoped_record_access(
        storage, intent, user, scope_field="context", not_found_detail="Intent not found"
    )
    recorded = IntentMemory(storage).complete(
        intent_id,
        actor_id=user.user_id,
        channel=WriteChannel.REST,
    )
    if not recorded:
        raise HTTPException(status_code=404, detail="Intent not found")
    append_audit_event(
        storage,
        event_type="intent.outcome_recorded",
        actor_id=user.user_id,
        repo_id=intent.get("repo_id"),
        target_type="intent",
        target_id=intent_id,
        metadata={"outcome": "completed", "authoritative": False, "status_changed": False},
    )
    return {
        "id": intent_id,
        "status": intent.get("status", "active"),
        "authoritative": False,
        "status_changed": False,
        "outcome_recorded": True,
    }


@router.post("/{intent_id}/close")
async def close_intent(
    request: Request, intent_id: str, user: UserContext = Depends(get_current_user)
):
    storage = request.app.state.storage
    intent = _find_intent(storage, intent_id)
    require_scoped_record_access(
        storage, intent, user, scope_field="context", not_found_detail="Intent not found"
    )
    recorded = IntentMemory(storage).close(
        intent_id,
        actor_id=user.user_id,
        channel=WriteChannel.REST,
    )
    if not recorded:
        raise HTTPException(status_code=404, detail="Intent not found")
    append_audit_event(
        storage,
        event_type="intent.outcome_recorded",
        actor_id=user.user_id,
        repo_id=intent.get("repo_id"),
        target_type="intent",
        target_id=intent_id,
        metadata={"outcome": "closed", "authoritative": False, "status_changed": False},
    )
    return {
        "id": intent_id,
        "status": intent.get("status", "active"),
        "authoritative": False,
        "status_changed": False,
        "outcome_recorded": True,
    }


@router.post("/{intent_id}/evaluate")
async def evaluate_intent(
    request: Request,
    intent_id: str,
    payload: IntentEvaluationRequest,
    user: UserContext = Depends(get_current_user),
):
    storage = request.app.state.storage
    intent = require_scoped_record_access(
        storage,
        _find_intent(storage, intent_id),
        user,
        scope_field="context",
        not_found_detail="Intent not found",
    )
    result = request.app.state.intent_evaluator.evaluate(
        intent,
        summary=payload.summary,
        memory_ids=payload.memory_ids,
        actor_id=user.user_id,
        allow_auto_complete=payload.model_dump()["allow_auto_complete"],
    )
    append_audit_event(
        storage,
        event_type=f"intent.evaluation_{result['decision']}",
        actor_id=user.user_id,
        repo_id=intent.get("repo_id"),
        target_type="intent",
        target_id=intent_id,
        metadata={
            "confidence": result["confidence"],
            "objective_evidence": result["objective_evidence"],
            "evaluator_version": result["evaluator_version"],
        },
    )
    return result


@router.get("/completion-suggestions")
async def list_completion_suggestions(
    request: Request,
    repo_id: str = None,
    user: UserContext = Depends(get_current_user),
):
    storage = request.app.state.storage
    config = load_config()
    target_repo_id = repo_id or config.repo_id
    require_repo_scope_access(storage, target_repo_id, user)
    suggestions = []
    for intent in storage.get_active_intents(repo_id=target_repo_id, status="active"):
        if not can_access_scoped_record(storage, intent, user, scope_field="context"):
            continue
        evaluation = (intent.get("context") or {}).get("completion_evaluation") or {}
        if evaluation.get("decision") == "suggested":
            suggestions.append(_intent_response_payload(intent))
    return suggestions


@router.post("/{intent_id}/reopen")
async def reopen_intent(
    request: Request,
    intent_id: str,
    user: UserContext = Depends(get_current_user),
):
    storage = request.app.state.storage
    intent = require_scoped_record_access(
        storage,
        _find_intent(storage, intent_id),
        user,
        scope_field="context",
        not_found_detail="Intent not found",
    )
    recorded = IntentMemory(storage).record_outcome(
        intent_id,
        "active",
        actor_id=user.user_id,
        channel=WriteChannel.REST,
    )
    if not recorded:
        raise HTTPException(status_code=404, detail="Intent not found")
    append_audit_event(
        storage,
        event_type="intent.outcome_recorded",
        actor_id=user.user_id,
        repo_id=intent.get("repo_id"),
        target_type="intent",
        target_id=intent_id,
        metadata={"outcome": "active", "authoritative": False, "status_changed": False},
    )
    return {
        "id": intent_id,
        "status": intent.get("status", "active"),
        "authoritative": False,
        "status_changed": False,
        "outcome_recorded": True,
    }
