from fastapi import APIRouter, Body, Depends, HTTPException, Request, status

from visp_memory.core.storage import SessionCompletionStatus
from visp_memory.server.auth import UserContext, get_current_user
from visp_memory.server.authorization import (
    can_access_scoped_record,
    require_repo_scope_access,
)
from visp_memory.server.routers.platform import append_audit_event
from visp_memory.server.schemas import (
    SessionCompleteRequest,
    SessionCompleteResponse,
    SessionCreateRequest,
    SessionCreateResponse,
    SessionResponse,
)

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.post("", response_model=SessionCreateResponse)
async def start_session(
    request: Request,
    payload: SessionCreateRequest | None = Body(default=None),
    user: UserContext = Depends(get_current_user),
):
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="repo_id is required",
        )
    storage = request.app.state.storage
    require_repo_scope_access(storage, payload.repo_id, user)
    session_id = storage.start_session(
        owner_id=user.user_id,
        team_id=user.team_id,
        repo_id=payload.repo_id,
    )
    if not session_id:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Storage backend does not support sessions",
        )
    return SessionCreateResponse(id=session_id)


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(
    request: Request,
    session_id: str,
    user: UserContext = Depends(get_current_user),
):
    session = request.app.state.storage.get_session(session_id)
    if (
        not session
        or not session.get("owner_id")
        or session.get("owner_id") != user.user_id
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    require_repo_scope_access(request.app.state.storage, session.get("repo_id"), user)
    return session


@router.post(
    "/{session_id}/complete",
    response_model=SessionCompleteResponse,
    response_model_exclude_none=True,
)
async def complete_session(
    request: Request,
    session_id: str,
    payload: SessionCompleteRequest,
    user: UserContext = Depends(get_current_user),
):
    storage = request.app.state.storage
    session = storage.get_session(session_id)
    if (
        not session
        or not session.get("owner_id")
        or not session.get("repo_id")
        or session.get("owner_id") != user.user_id
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    repo_id = session["repo_id"]
    require_repo_scope_access(storage, repo_id, user)
    for memory_id in payload.memory_ids:
        memory = storage.get_memory(memory_id)
        if (
            not memory
            or memory.get("repo_id") != repo_id
            or not can_access_scoped_record(storage, memory, user, scope_field="metadata")
        ):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Memory not found",
            )

    completed = storage.end_session(session_id, payload.summary, payload.memory_ids)
    if completed is SessionCompletionStatus.NOT_FOUND:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    if completed is SessionCompletionStatus.ALREADY_COMPLETED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Session is already completed",
        )

    evaluations = []
    repo_evaluations = request.app.state.intent_evaluator.evaluate_repository(
        repo_id,
        summary=payload.summary,
        memory_ids=payload.memory_ids,
        actor_id=user.user_id,
        intent_filter=lambda intent: can_access_scoped_record(
            storage, intent, user, scope_field="context"
        ),
    )
    evaluations.extend(repo_evaluations)
    for evaluation in repo_evaluations:
        append_audit_event(
            storage,
            event_type=f"intent.evaluation_{evaluation['decision']}",
            actor_id=user.user_id,
            repo_id=repo_id,
            target_type="intent",
            target_id=evaluation["intent_id"],
            metadata={"confidence": evaluation["confidence"], "session_id": session_id},
        )
    return SessionCompleteResponse(
        id=session_id,
        intent_evaluations=evaluations or None,
    )
