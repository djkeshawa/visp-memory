"""Administrator controls for background memory consolidation."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from starlette.concurrency import run_in_threadpool

from visp_memory.core.dreaming import Dreaming
from visp_memory.server.auth import UserContext, get_current_user
from visp_memory.server.authorization import has_admin_privileges, require_repo_writable

router = APIRouter(prefix="/dreaming", tags=["dreaming"])


class Schedule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    interval_hours: Literal[6, 12, 24, 168] = 24


class Review(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["archive", "dismiss"]


def service(request, repo_id, user):
    if not has_admin_privileges(user):
        raise HTTPException(403, "Dreaming controls require an administrator")
    require_repo_writable(request.app.state.storage, repo_id, user)
    try:
        return Dreaming(request.app.state.storage)
    except NotImplementedError as error:
        raise HTTPException(501, str(error)) from error


async def execute(method, *args, **kwargs):
    try:
        return await run_in_threadpool(method, *args, **kwargs)
    except ValueError as error:
        raise HTTPException(409, str(error)) from error


@router.get("/{repo_id}")
async def status(request: Request, repo_id: str, user: UserContext = Depends(get_current_user)):
    dream = service(request, repo_id, user)
    return {
        "settings": await execute(dream.settings, repo_id),
        "runs": await execute(dream.history, repo_id),
    }


@router.put("/{repo_id}/schedule")
async def schedule(
    request: Request, repo_id: str, body: Schedule, user: UserContext = Depends(get_current_user)
):
    dream = service(request, repo_id, user)
    return await execute(dream.configure, repo_id, **body.model_dump())


@router.get("/{repo_id}/preview")
async def preview(request: Request, repo_id: str, user: UserContext = Depends(get_current_user)):
    return await execute(service(request, repo_id, user).preview, repo_id)


@router.post("/{repo_id}/run")
async def run(request: Request, repo_id: str, user: UserContext = Depends(get_current_user)):
    return await execute(service(request, repo_id, user).run, repo_id, actor_id=user.user_id)


@router.post("/{repo_id}/runs/{run_id}/proposals/{proposal_id}")
async def review(
    request: Request,
    repo_id: str,
    run_id: str,
    proposal_id: str,
    body: Review,
    user: UserContext = Depends(get_current_user),
):
    return await execute(
        service(request, repo_id, user).review,
        repo_id,
        run_id,
        proposal_id,
        body.decision,
        actor_id=user.user_id,
    )


@router.post("/{repo_id}/actions/{action_id}/undo")
async def undo(
    request: Request, repo_id: str, action_id: str, user: UserContext = Depends(get_current_user)
):
    return await execute(
        service(request, repo_id, user).undo, repo_id, action_id, actor_id=user.user_id
    )
