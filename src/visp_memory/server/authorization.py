from typing import Any, Optional

from fastapi import HTTPException, status

from visp_memory.core.eligibility import UNSCOPED_REPO_ID
from visp_memory.server.auth import UserContext


def _get_repository(storage: Any, repo_id: Optional[str]) -> Optional[dict[str, Any]]:
    if not repo_id or not hasattr(storage, "get_repository"):
        return None
    return storage.get_repository(repo_id)


def require_admin(user: UserContext) -> None:
    """Require an administrative principal for global or destructive operations.

    In local mode (auth disabled) get_current_user returns an admin context, so
    this is a no-op there; with auth enabled, anonymous and ordinary team users
    are rejected.
    """
    if not user.is_admin or (
        user.auth_type == "pat" and "admin" not in user.scopes and "*" not in user.scopes
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator privileges are required for this operation",
        )


def require_repo_scope_access(
    storage: Any,
    repo_id: Optional[str],
    user: UserContext,
    *,
    allow_global: bool = False,
) -> None:
    """Hide registered repositories outside the current non-admin user's team."""
    if not isinstance(repo_id, str) or not repo_id.strip():
        if allow_global:
            require_admin(user)
            return
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="repo_id is required",
        )

    # The reserved bucket is not a repository, and this gate only ever checked
    # for a BLANK scope — so the reserved name, being a perfectly good non-blank
    # string, walked straight through. What happened next was decided by whether
    # each individual endpoint happened to call require_repo_id: GET /memories
    # and GET /graph did not, and served quarantined rows; POST /recall did, and
    # answered 500. That is not a security model, it is a coincidence.
    #
    # Rows land in this bucket carrying Provenance.UNKNOWN, from the v2->v3
    # migration or from a write that never named a project. Refusing to serve
    # them is what keeps unattributed content from reaching a model as though it
    # were project knowledge, and no route may go around it. Refusing writes to
    # it matters just as much: otherwise a client can deliberately park content
    # where no recall will ever find it.
    if repo_id.strip() == UNSCOPED_REPO_ID:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"{UNSCOPED_REPO_ID} is a reserved scope for quarantined memories of unknown "
                "origin. It cannot be read from or written to. Use your project's repo_id."
            ),
        )
    if user.auth_type == "pat" and user.repo_ids and repo_id not in user.repo_ids:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository not found")
    if user.is_admin:
        return

    repo = _get_repository(storage, repo_id)
    if repo and repo.get("team_id") != user.team_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository not found")


def require_repo_writable(storage: Any, repo_id: Optional[str], user: UserContext) -> None:
    """Require access to a repository that is accepting new records."""
    require_repo_scope_access(storage, repo_id, user)
    repo = _get_repository(storage, repo_id)
    if repo and repo.get("status", "active") == "archived":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Repository is archived and does not accept new writes",
        )


def can_access_scoped_record(
    storage: Any,
    record: dict[str, Any],
    user: UserContext,
    *,
    scope_field: str,
) -> bool:
    """Return whether a memory/intent row belongs to the current user's team scope."""
    repo_id = record.get("repo_id")
    if user.auth_type == "pat" and user.repo_ids and repo_id not in user.repo_ids:
        return False
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
