from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, Request

from llm_memory.config import load_config
from llm_memory.server.auth import UserContext, get_current_user
from llm_memory.server.schemas import IntentCreate, IntentResponse

router = APIRouter(prefix="/intents", tags=["intents"])


@router.get("", response_model=List[IntentResponse])
async def list_intents(
    request: Request, repo_id: str = None, user: UserContext = Depends(get_current_user)
):
    storage = request.app.state.storage
    config = load_config()
    intents = storage.get_active_intents(repo_id=repo_id or config.repo_id)

    return [
        {
            "id": i["id"],
            "description": i["description"],
            "priority": i["priority"],
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

    # Add author attribution
    context = intent.context or {}
    context["author_id"] = user.user_id
    if user.team_id:
        context["team_id"] = user.team_id

    intent_id = storage.set_intent(
        description=intent.description,
        priority=intent.priority if hasattr(intent, "priority") else 0,
        repo_id=intent.repo_id or config.repo_id,
        context=context,
    )

    return {
        "id": intent_id,
        "description": intent.description,
        "priority": getattr(intent, "priority", 0),
        "repo_id": intent.repo_id or config.repo_id,
        "status": "active",
        "context": context,
        "created_at": datetime.now(),
    }
