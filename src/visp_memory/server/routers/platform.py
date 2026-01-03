from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request, status

from visp_memory.core.clock import utc_now
from visp_memory.server.auth import UserContext, get_current_user
from visp_memory.server.schemas import AuditLogEntry

router = APIRouter(prefix="/platform", tags=["platform"])


def append_audit_event(
    storage,
    *,
    event_type: str,
    actor_id: str = None,
    repo_id: str = None,
    target_type: str = None,
    target_id: str = None,
    metadata: dict = None,
) -> None:
    """Write an audit event when the storage backend supports it."""
    capabilities = storage.get_capabilities() if hasattr(storage, "get_capabilities") else None
    if (capabilities and capabilities.audit_log) or (
        capabilities is None and hasattr(storage, "append_audit_log")
    ):
        storage.append_audit_log(
            event_type=event_type,
            actor_id=actor_id,
            repo_id=repo_id,
            target_type=target_type,
            target_id=target_id,
            metadata=metadata or {},
        )


def _as_datetime(value):
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    return value or utc_now()


@router.get("/audit-log", response_model=List[AuditLogEntry])
async def list_audit_log(
    request: Request,
    actor_id: str = None,
    repo_id: str = None,
    event_type: str = None,
    limit: int = 100,
    user: UserContext = Depends(get_current_user),
):
    """List non-secret audit events for admin review."""
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Audit log access requires an admin user",
        )
    storage = request.app.state.storage
    capabilities = storage.get_capabilities() if hasattr(storage, "get_capabilities") else None
    if not (
        (capabilities and capabilities.audit_log)
        or (capabilities is None and hasattr(storage, "list_audit_logs"))
    ):
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Storage backend does not support audit logs",
        )

    rows = storage.list_audit_logs(
        actor_id=actor_id,
        repo_id=repo_id,
        event_type=event_type,
        limit=max(1, min(limit, 500)),
    )
    return [
        {
            "id": row["id"],
            "event_type": row["event_type"],
            "actor_id": row.get("actor_id"),
            "repo_id": row.get("repo_id"),
            "target_type": row.get("target_type"),
            "target_id": row.get("target_id"),
            "metadata": row.get("metadata") or {},
            "created_at": _as_datetime(row.get("created_at")),
        }
        for row in rows
    ]
