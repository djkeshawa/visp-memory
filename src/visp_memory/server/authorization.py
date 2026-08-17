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

    # An untenanted repository is not somebody else's repository. The store now
    # creates a row for every project scope a write names (it has no principal, so
    # the row carries no team), and that row has to stay indistinguishable from the
    # absent row it replaced — which this gate let straight through. Only a
    # repository that names an owning team can exclude anyone.
    repo = _get_repository(storage, repo_id)
    repo_team_id = repo.get("team_id") if repo else None
    if repo_team_id is not None and repo_team_id != user.team_id:
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


def _record_team_id(storage: Any, record: dict[str, Any], *, scope_field: str) -> Optional[str]:
    """The team that owns a memory/intent row: its own scope, else its repository's."""
    scope = record.get(scope_field) or {}
    if isinstance(scope, dict) and scope.get("team_id") is not None:
        return scope["team_id"]

    repo = _get_repository(storage, record.get("repo_id"))
    return repo.get("team_id") if repo else None


def can_access_scoped_record(
    storage: Any,
    record: dict[str, Any],
    user: UserContext,
    *,
    scope_field: str,
) -> bool:
    """Return whether a memory/intent row belongs to the current user's team scope.

    This is tenant equality, not tenant presence. A record that names no team, in a
    repository that names no team, belongs to nobody in particular — and the single
    user of a local store is nobody in particular too, so the two match.

    Requiring a team on both sides instead is what made the dashboard read zero
    against a store with memories in it: `serve` starts a loopback server in open
    local mode, whose principal is anonymous and teamless by design, so every row
    failed a comparison neither side was ever going to satisfy. `allow_anonymous`
    became a switch that granted 200s with empty bodies. The principal is still not
    an admin, so nothing here opens an admin surface.
    """
    repo_id = record.get("repo_id")
    if user.auth_type == "pat" and user.repo_ids and repo_id not in user.repo_ids:
        return False
    if user.is_admin:
        return True

    return _record_team_id(storage, record, scope_field=scope_field) == user.team_id


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
