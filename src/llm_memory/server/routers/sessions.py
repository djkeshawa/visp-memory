from fastapi import APIRouter, Depends, HTTPException, Request, status

from llm_memory.server.auth import UserContext, get_current_user
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


@router.post("/{session_id}/complete", response_model=SessionCompleteResponse)
async def complete_session(
    request: Request,
    session_id: str,
    payload: SessionCompleteRequest,
    user: UserContext = Depends(get_current_user),
):
    del user
    completed = request.app.state.storage.end_session(
        session_id,
        payload.summary,
        payload.memory_ids,
    )
    if completed is False:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return SessionCompleteResponse(id=session_id)
