"""Authenticated, ordered reports from the workflow that owns an intent."""

from fastapi import APIRouter, Depends, HTTPException, Request

from visp_memory.core.intent_workflow import IntentWorkflowReport
from visp_memory.server.auth import UserContext, get_current_user
from visp_memory.server.authorization import (
    has_admin_privileges,
    require_repo_writable,
    require_scoped_record_access,
)
from visp_memory.server.routers.intents import _find_intent
from visp_memory.server.routers.platform import append_audit_event

router = APIRouter(prefix="/intents", tags=["intents"])


@router.post("/{intent_id}/workflow-status")
async def report_workflow_status(
    request: Request,
    intent_id: str,
    payload: IntentWorkflowReport,
    user: UserContext = Depends(get_current_user),
):
    if user.auth_type not in {"session", "pat", "jwt", "api_key"}:
        raise HTTPException(403, "Workflow reports require an authenticated account or API token")
    storage = request.app.state.storage
    intent = require_scoped_record_access(
        storage,
        _find_intent(storage, intent_id),
        user,
        scope_field="context",
        not_found_detail="Intent not found",
    )
    require_repo_writable(storage, intent["repo_id"], user)
    author = (intent.get("context") or {}).get("author_id")
    if not has_admin_privileges(user) and author != user.user_id:
        raise HTTPException(403, "Only the intent owner can connect a workflow reporter")
    try:
        result = storage.report_intent_workflow(
            intent_id, payload.model_dump(mode="json"), actor_id=user.user_id, channel="rest"
        )
    except NotImplementedError as error:
        raise HTTPException(501, str(error)) from error
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    except LookupError as error:
        raise HTTPException(404, "Intent not found") from error
    if result["applied"]:
        append_audit_event(
            storage,
            event_type="intent.workflow_status_reported",
            actor_id=user.user_id,
            repo_id=intent["repo_id"],
            target_type="intent",
            target_id=intent_id,
            metadata={
                "source": payload.source,
                "event_id": payload.event_id,
                "revision": payload.revision,
                "status": payload.status,
            },
        )
    return {"id": intent_id, **result, "authoritative": False}
