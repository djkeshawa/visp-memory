from typing import Any, Callable, Dict, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Request, status

from visp_memory.config import load_config
from visp_memory.core.attribution import without_written_by
from visp_memory.server.auth import UserContext, get_current_user
from visp_memory.server.authorization import (
    require_repo_scope_access,
    require_repo_writable,
    require_scoped_record_access,
)
from visp_memory.server.request_scope import request_repo_id
from visp_memory.server.schemas_recall import (
    RecallEventCreate,
    RecallEventCreated,
    RecallUtilityResetResponse,
)
from visp_memory.server.scoped_utility import (
    inspect_visible_utility,
    reset_visible_utility,
    verify_visible_utility,
)

router = APIRouter(prefix="/recall-events", tags=["recall-utility"])
T = TypeVar("T")


def _request_repo_id(requested: str | None) -> str | None:
    return request_repo_id(requested, load_config())


def _require_memory_in_repo(storage, memory_id: str, repo_id: str, user: UserContext) -> dict:
    memory = storage.peek_memory(memory_id)
    if not memory or memory.get("repo_id") != repo_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Memory not found",
        )
    return require_scoped_record_access(
        storage,
        memory,
        user,
        scope_field="metadata",
        not_found_detail="Memory not found",
    )


def _require_optional_memory(
    storage,
    memory_id: str | None,
    repo_id: str,
    user: UserContext,
) -> None:
    if memory_id is not None:
        _require_memory_in_repo(storage, memory_id, repo_id, user)


def _call_utility(operation: Callable[[], T]) -> T:
    try:
        return operation()
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error


@router.post("", response_model=RecallEventCreated)
def log_recall_event(
    request: Request,
    event: RecallEventCreate,
    user: UserContext = Depends(get_current_user),
):
    storage = request.app.state.storage
    repo_id = _request_repo_id(event.repo_id)
    require_repo_writable(storage, repo_id, user)
    _require_memory_in_repo(storage, event.memory_id, repo_id, user)
    event_id = _call_utility(
        lambda: storage.log_recall_event(
            memory_id=event.memory_id,
            event_type=event.event_type,
            repo_id=repo_id,
            query=event.query,
            task_id=event.task_id,
            outcome=event.outcome,
            metadata=without_written_by(event.metadata),
        )
    )
    return {"id": event_id}


@router.get("/utility", response_model=Dict[str, Any])
def inspect_recall_utility(
    request: Request,
    memory_id: str = None,
    repo_id: str = None,
    event_type: str = None,
    limit: int = 50,
    user: UserContext = Depends(get_current_user),
):
    storage = request.app.state.storage
    resolved_repo_id = _request_repo_id(repo_id)
    require_repo_scope_access(storage, resolved_repo_id, user)
    _require_optional_memory(storage, memory_id, resolved_repo_id, user)
    return _call_utility(
        lambda: inspect_visible_utility(
            storage, user,
            memory_id=memory_id,
            repo_id=resolved_repo_id,
            event_type=event_type,
            limit=max(0, limit),
        )
    )


@router.delete("", response_model=RecallUtilityResetResponse)
def reset_recall_utility(
    request: Request,
    memory_id: str = None,
    repo_id: str = None,
    event_type: str = None,
    user: UserContext = Depends(get_current_user),
):
    storage = request.app.state.storage
    resolved_repo_id = _request_repo_id(repo_id)
    require_repo_writable(storage, resolved_repo_id, user)
    _require_optional_memory(storage, memory_id, resolved_repo_id, user)
    deleted = _call_utility(
        lambda: reset_visible_utility(
            storage, user,
            memory_id=memory_id,
            repo_id=resolved_repo_id,
            event_type=event_type,
        )
    )
    return {"deleted": deleted}


@router.get("/verify", response_model=Dict[str, Any])
def verify_recall_utility(
    request: Request,
    memory_id: str = None,
    repo_id: str = None,
    event_type: str = None,
    user: UserContext = Depends(get_current_user),
):
    storage = request.app.state.storage
    resolved_repo_id = _request_repo_id(repo_id)
    require_repo_scope_access(storage, resolved_repo_id, user)
    _require_optional_memory(storage, memory_id, resolved_repo_id, user)
    return _call_utility(
        lambda: verify_visible_utility(
            storage, user,
            memory_id=memory_id,
            repo_id=resolved_repo_id,
            event_type=event_type,
        )
    )
