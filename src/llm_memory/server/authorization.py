from typing import Any, Optional

from fastapi import HTTPException, status

from llm_memory.server.auth import UserContext


def _get_repository(storage: Any, repo_id: Optional[str]) -> Optional[dict[str, Any]]:
    if not repo_id or not hasattr(storage, "get_repository"):
        return None
    return storage.get_repository(repo_id)


def require_repo_scope_access(storage: Any, repo_id: Optional[str], user: UserContext) -> None:
    """Hide registered repositories outside the current non-admin user's team."""
    if user.is_admin:
        return

    repo = _get_repository(storage, repo_id)
    if repo and repo.get("team_id") != user.team_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository not found")


def can_access_scoped_record(
    storage: Any,
    record: dict[str, Any],
    user: UserContext,
    *,
    scope_field: str,
) -> bool:
    """Return whether a memory/intent row belongs to the current user's team scope."""
    if user.is_admin:
        return True
    if not user.team_id:
        return False

    scope = record.get(scope_field) or {}
    if isinstance(scope, dict) and scope.get("team_id") is not None:
        return scope.get("team_id") == user.team_id

    repo = _get_repository(storage, record.get("repo_id"))
    if repo:
        return repo.get("team_id") == user.team_id

    return False


def require_scoped_record_access(
    storage: Any,
    record: Optional[dict[str, Any]],
    user: UserContext,
    *,
    scope_field: str,
    not_found_detail: str,
) -> dict[str, Any]:
    if not record or not can_access_scoped_record(
        storage, record, user, scope_field=scope_field
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=not_found_detail)
    return record
