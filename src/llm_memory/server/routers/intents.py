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
from llm_memory.server.schemas import IntentCreate, IntentResponse

router = APIRouter(prefix="/intents", tags=["intents"])


@router.get("", response_model=List[IntentResponse])
async def list_intents(
    request: Request, repo_id: str = None, user: UserContext = Depends(get_current_user)
):
    storage = request.app.state.storage
    config = load_config()
    intent_repo_id = repo_id or config.repo_id
    require_repo_scope_access(storage, intent_repo_id, user)
    intents = storage.get_active_intents(repo_id=intent_repo_id)
    intents = [
        i for i in intents if can_access_scoped_record(storage, i, user, scope_field="context")
    ]

    return [
        {
            "id": i["id"],
            "description": i["description"],
            "priority": i["priority"],
            "repo_id": i.get("repo_id"),
            "context": i.get("context", {}),
            "status": i["status"],
            "created_at": i["created_at"],
        }
        for i in intents
    ]


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
    }


@router.post("/{intent_id}/complete")
async def complete_intent(
    request: Request, intent_id: str, user: UserContext = Depends(get_current_user)
):
    storage = request.app.state.storage
    intent = next(
        (item for item in storage.get_active_intents(repo_id=None) if item["id"] == intent_id),
        None,
    )
    require_scoped_record_access(
        storage, intent, user, scope_field="context", not_found_detail="Intent not found"
    )
    success = storage.complete_intent(intent_id)
    if not success:
        raise HTTPException(status_code=404, detail="Intent not found")
    return {"status": "completed", "id": intent_id}
