from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Request, status

from visp_memory.config import load_config
from visp_memory.server.auth import UserContext, get_current_user
from visp_memory.server.authorization import (
    can_access_scoped_record,
    require_repo_scope_access,
    require_scoped_record_access,
)
from visp_memory.server.request_scope import request_repo_id
from visp_memory.server.schemas_recall import TurnKeySearchRequest

router = APIRouter(tags=["memory-inspection"])


def _resolved_repo_id(requested: str | None) -> str | None:
    return request_repo_id(requested, load_config())


def _scoped_inspection(storage, method_name: str, repo_id: str) -> dict:
    method = getattr(storage, method_name, None)
    data_dir = getattr(storage, "data_dir", None)
    if not callable(method) or data_dir is None:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=f"Storage backend does not support {method_name}",
        )
    return method(data_dir, repo_id=repo_id)


@router.get("/memories/{memory_id}/peek", response_model=Dict[str, Any])
async def peek_memory(
    request: Request,
    memory_id: str,
    repo_id: str = None,
    user: UserContext = Depends(get_current_user),
):
    storage = request.app.state.storage
    resolved_repo_id = _resolved_repo_id(repo_id)
    require_repo_scope_access(storage, resolved_repo_id, user)
    memory = storage.peek_memory(memory_id)
    if not memory or memory.get("repo_id") != resolved_repo_id:
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


@router.post("/turn-keys/search", response_model=List[Dict[str, Any]])
async def search_turn_keys(
    request: Request,
    search: TurnKeySearchRequest,
    user: UserContext = Depends(get_current_user),
):
    storage = request.app.state.storage
    repo_id = _resolved_repo_id(search.repo_id)
    require_repo_scope_access(storage, repo_id, user)
    hits = storage.search_turn_keys(
        search.query,
        repo_id=repo_id,
        limit=search.limit,
        status=search.status,
    )
    return [
        hit
        for hit in hits
        if isinstance(hit.get("memory"), dict)
        and hit["memory"].get("repo_id") == repo_id
        and can_access_scoped_record(
            storage,
            hit["memory"],
            user,
            scope_field="metadata",
        )
    ]


@router.get("/intents/usage", response_model=Dict[str, Any])
async def inspect_intent_usage(
    request: Request,
    repo_id: str = None,
    user: UserContext = Depends(get_current_user),
):
    storage = request.app.state.storage
    resolved_repo_id = _resolved_repo_id(repo_id)
    require_repo_scope_access(storage, resolved_repo_id, user)
    return _scoped_inspection(storage, "inspect_intent_usage", resolved_repo_id)


@router.get("/repos/{repo_id}/registration", response_model=Dict[str, Any])
async def inspect_repository_registration(
    request: Request,
    repo_id: str,
    user: UserContext = Depends(get_current_user),
):
    storage = request.app.state.storage
    require_repo_scope_access(storage, repo_id, user)
    return _scoped_inspection(
        storage,
        "inspect_repository_registration",
        repo_id,
    )


@router.get("/diagnostics/capabilities", response_model=Dict[str, bool])
async def get_storage_capabilities(
    request: Request,
    user: UserContext = Depends(get_current_user),
):
    del user
    return request.app.state.storage.get_capabilities().to_dict()
