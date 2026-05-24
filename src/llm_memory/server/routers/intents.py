from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request

from llm_memory.config import load_config
from llm_memory.server.auth import UserContext, get_current_user
from llm_memory.server.authorization import (
    can_access_scoped_record,
    require_repo_scope_access,
    require_scoped_record_access,
)
from llm_memory.server.routers.platform import append_audit_event
from llm_memory.server.schemas import IntentCreate, IntentResponse, IntentUpdate

router = APIRouter(prefix="/intents", tags=["intents"])


def _as_datetime(value):
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    return value or datetime.now()


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
    require_repo_scope_access(storage, intent_repo_id, user)

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
        "created_at": datetime.now(),
        "updated_at": datetime.now(),
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

    if "context" in update_data and not user.is_admin:
        context = dict(update_data["context"] or {})
        existing_context = intent.get("context") or {}
        for reserved_key in ("author_id", "team_id"):
            if reserved_key in existing_context:
                context[reserved_key] = existing_context[reserved_key]
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
    success = storage.complete_intent(intent_id)
    if not success:
        raise HTTPException(status_code=404, detail="Intent not found")
    append_audit_event(
        storage,
        event_type="intent.completed",
        actor_id=user.user_id,
        repo_id=intent.get("repo_id"),
        target_type="intent",
        target_id=intent_id,
    )
    return {"status": "completed", "id": intent_id}


@router.post("/{intent_id}/close")
async def close_intent(
    request: Request, intent_id: str, user: UserContext = Depends(get_current_user)
):
    storage = request.app.state.storage
    intent = _find_intent(storage, intent_id)
    require_scoped_record_access(
        storage, intent, user, scope_field="context", not_found_detail="Intent not found"
    )
    success = storage.update_intent(intent_id, status="closed")
    if not success:
        raise HTTPException(status_code=404, detail="Intent not found")
    append_audit_event(
        storage,
        event_type="intent.closed",
        actor_id=user.user_id,
        repo_id=intent.get("repo_id"),
        target_type="intent",
        target_id=intent_id,
    )
    return {"status": "closed", "id": intent_id}
