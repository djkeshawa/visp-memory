"""Authenticated, ordered reports from the workflow that owns an intent."""

from fastapi import APIRouter, Depends, HTTPException, Request

from visp_memory.core.intent_workflow import IntentWorkflowReport
from visp_memory.core.trust import LOCAL_WORKFLOW_ACTOR, WriteChannel
from visp_memory.server.auth import UserContext, get_current_user
from visp_memory.server.authorization import (
    has_admin_privileges,
    require_repo_writable,
    require_scoped_record_access,
)
from visp_memory.server.routers.intents import _find_intent
from visp_memory.server.routers.platform import append_audit_event
from visp_memory.server.write_channel import request_write_channel

router = APIRouter(prefix="/intents", tags=["intents"])
AUTHENTICATED_TYPES = frozenset({"session", "pat", "jwt", "api_key"})


@router.post("/{intent_id}/workflow-status")
async def report_workflow_status(
    request: Request,
    intent_id: str,
    payload: IntentWorkflowReport,
    user: UserContext = Depends(get_current_user),
):
    # A loopback peer is not proof of the owner (any local process, other OS user
    # or DNS-rebinding page has one), and an intent's status belongs to the
    # external workflow authority. An anonymous caller therefore has to present
    # the owner token, exactly as the other local maintenance routes require.
    if user.auth_type not in AUTHENTICATED_TYPES and not user.owner_maintenance:
        raise HTTPException(
            403,
            "Workflow reports require an authenticated account, an API token, "
            "or the local owner token",
        )
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
    if not user.is_local_owner and not has_admin_privileges(user) and author != user.user_id:
        raise HTTPException(403, "Only the intent owner can connect a workflow reporter")
    actor_id = LOCAL_WORKFLOW_ACTOR if user.is_local_owner else user.user_id
    try:
        result = storage.report_intent_workflow(
            intent_id,
            payload.model_dump(mode="json"),
            actor_id=actor_id,
            channel=request_write_channel(user, default=WriteChannel.REST).value,
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
            actor_id=actor_id,
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
