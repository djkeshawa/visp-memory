from fastapi import APIRouter, Depends, HTTPException, Request, status

from llm_memory.server.auth import UserContext, get_current_user
from llm_memory.server.routers.platform import append_audit_event
from llm_memory.server.schemas import (
    SessionCompleteRequest,
    SessionCompleteResponse,
    SessionCreateResponse,
)

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.post("", response_model=SessionCreateResponse)
async def start_session(
    request: Request,
    user: UserContext = Depends(get_current_user),
):
    del user
    session_id = request.app.state.storage.start_session()
    if not session_id:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Storage backend does not support sessions",
        )
    return SessionCreateResponse(id=session_id)


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
    completed = request.app.state.storage.end_session(
        session_id,
        payload.summary,
        payload.memory_ids,
    )
    if completed is False:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    repo_ids = {
        memory.get("repo_id")
        for memory_id in payload.memory_ids
        if (memory := request.app.state.storage.get_memory(memory_id))
        and memory.get("repo_id")
    }
    evaluations = []
    for repo_id in sorted(repo_ids):
        repo_evaluations = request.app.state.intent_evaluator.evaluate_repository(
            repo_id,
            summary=payload.summary,
            memory_ids=payload.memory_ids,
            actor_id=user.user_id,
        )
        evaluations.extend(repo_evaluations)
        for evaluation in repo_evaluations:
            append_audit_event(
                request.app.state.storage,
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
