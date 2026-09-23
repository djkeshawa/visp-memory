import json
import os
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from visp_memory.core.clock import utc_now
from visp_memory.core.cross_repo import CrossRepoContext
from visp_memory.core.repository import (
    DependencyType,
    Repository,
    RepositoryDependency,
)
from visp_memory.core.storage import iter_repository_memories
from visp_memory.server.auth import UserContext, get_current_user
from visp_memory.server.authorization import (
    can_access_scoped_record,
    require_admin,
    require_repo_scope_access,
)
from visp_memory.server.repository_access import AuthorizedRepositoryManager
from visp_memory.server.routers.platform import append_audit_event
from visp_memory.server.schemas import (
    DependencyCreate,
    ProjectScopeResponse,
    RepositoryCreate,
    RepositoryResponse,
)

router = APIRouter(prefix="/repos", tags=["repositories"])


@router.post("", response_model=RepositoryResponse)
async def register_repository(
    request: Request,
    repo: RepositoryCreate,
    user: UserContext = Depends(get_current_user),
):
    """Register a new repository."""
    repo_mgr = AuthorizedRepositoryManager(request.app.state.storage, user)

    repo_obj = Repository(
        id=repo.id or repo.name.lower().replace(" ", "-"),
        name=repo.name,
        url=repo.url,
        description=repo.description,
        tech_stack=repo.tech_stack or [],
        team_id=user.team_id,
        metadata=repo.metadata or {},
    )

    require_repo_scope_access(request.app.state.storage, repo_obj.id, user)
    try:
        repo_id = repo_mgr.register(repo_obj)
    except NotImplementedError as e:
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

    return {
        **repo_obj.__dict__,
        "id": repo_id,
        "created_at": utc_now(),
    }


@router.get("", response_model=List[RepositoryResponse])
async def list_repositories(
    request: Request,
    team_id: Optional[str] = None,
    include_archived: bool = False,
    user: UserContext = Depends(get_current_user),
):
    """List all repositories."""
    repo_mgr = AuthorizedRepositoryManager(request.app.state.storage, user)
    repos = repo_mgr.list_all(team_id=team_id, include_archived=include_archived)

    return [
        {
            **r.__dict__,
            "created_at": r.created_at or utc_now(),
        }
        for r in repos
    ]


@router.get("/scopes", response_model=List[ProjectScopeResponse])
async def list_project_scopes(request: Request, user: UserContext = Depends(get_current_user)):
    """List repository/project scopes available for dashboard filtering."""
    storage = request.app.state.storage
    repo_mgr = AuthorizedRepositoryManager(storage, user)
    return repo_mgr.project_scopes()


@router.get("/{repo_id}", response_model=RepositoryResponse)
async def get_repository(
    request: Request,
    repo_id: str,
    user: UserContext = Depends(get_current_user),
):
    """Get repository details."""
    repo_mgr = AuthorizedRepositoryManager(request.app.state.storage, user)
    repo = repo_mgr.require(repo_id)

    return {
        **repo.__dict__,
        "created_at": repo.created_at or utc_now(),
    }


@router.post("/{repo_id}/archive")
async def archive_repository(
    request: Request,
    repo_id: str,
    user: UserContext = Depends(get_current_user),
):
    require_admin(user)
    manager = AuthorizedRepositoryManager(request.app.state.storage, user)
    repository = manager.require(repo_id)
    if repository.status != "archived" and not manager.archive(repo_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository not found")
    append_audit_event(
        request.app.state.storage,
        event_type="repository.archived",
        actor_id=user.user_id,
        repo_id=repo_id,
        target_type="repository",
        target_id=repo_id,
    )
    return {"status": "archived", "id": repo_id}


@router.post("/{repo_id}/restore")
async def restore_repository(
    request: Request,
    repo_id: str,
    user: UserContext = Depends(get_current_user),
):
    require_admin(user)
    manager = AuthorizedRepositoryManager(request.app.state.storage, user)
    manager.require(repo_id)
    if not manager.restore(repo_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository not found")
    append_audit_event(
        request.app.state.storage,
        event_type="repository.restored",
        actor_id=user.user_id,
        repo_id=repo_id,
        target_type="repository",
        target_id=repo_id,
    )
    return {"status": "active", "id": repo_id}


def _repository_purge_preview(storage, repo_id: str) -> dict:
    memories = list(iter_repository_memories(storage, repo_id))
    intents = storage.get_active_intents(repo_id=repo_id, status="all")
    relationships = storage.get_all_relationships(repo_id=repo_id)
    return {
        "repo_id": repo_id,
        "memories": len(memories),
        "intents": len(intents),
        "relationships": len(relationships),
        "content_bytes": sum(
            len(memory.get("content", "").encode("utf-8")) for memory in memories
        ),
    }


@router.get("/{repo_id}/purge-preview")
async def preview_repository_purge(
    request: Request,
    repo_id: str,
    user: UserContext = Depends(get_current_user),
):
    require_admin(user)
    AuthorizedRepositoryManager(request.app.state.storage, user).require(repo_id)
    return _repository_purge_preview(request.app.state.storage, repo_id)


# A purge is irreversible, so the pre-purge backup is a deliberate safety net.
# But it is a complete plaintext copy of everything just purged, and it used to be
# written world-readable with no expiry — so "purge this repository" left the
# purged content sitting on disk indefinitely (MG-036).
BACKUP_RETENTION_DAYS = 30


def _export_repository_backup(storage, repo_id: str, backup_dir) -> str:
    backup_dir.mkdir(parents=True, exist_ok=True)
    # Owner-only, and set before anything is written into it.
    os.chmod(backup_dir, 0o700)

    created_at = utc_now()
    timestamp = created_at.strftime("%Y%m%dT%H%M%SZ")
    backup_path = backup_dir / f"project-{repo_id}-{timestamp}.json"
    payload = {
        "format": "visp-memory-project-backup-v1",
        "created_at": created_at.isoformat(),
        # Retention is recorded in the artifact itself so a sweeper — or an
        # operator — can tell when it stopped being justified without needing the
        # server that wrote it.
        "retention": {
            "expires_at": (created_at + timedelta(days=BACKUP_RETENTION_DAYS)).isoformat(),
            "reason": "pre-purge safety copy",
            "contains_purged_plaintext": True,
        },
        "repository": storage.get_repository(repo_id),
        "memories": list(iter_repository_memories(storage, repo_id)),
        "intents": storage.get_active_intents(repo_id=repo_id, status="all"),
        "relationships": storage.get_all_relationships(repo_id=repo_id),
    }
    serialized = json.dumps(payload, indent=2, default=str)

    # Create owner-only from the start rather than writing then chmod-ing, so the
    # content is never briefly readable by anyone else.
    fd = os.open(backup_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(serialized)

    json.loads(backup_path.read_text(encoding="utf-8"))
    return backup_path.name


@router.delete("/{repo_id}")
async def purge_repository(
    request: Request,
    repo_id: str,
    confirmation: str,
    user: UserContext = Depends(get_current_user),
):
    require_admin(user)
    if confirmation != repo_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Confirmation must exactly match the repository ID",
        )
    manager = AuthorizedRepositoryManager(request.app.state.storage, user)
    repository = manager.require(repo_id)
    preview = _repository_purge_preview(request.app.state.storage, repo_id)
    backup_name = _export_repository_backup(
        request.app.state.storage,
        repo_id,
        request.app.state.auth_store.path.parent / "backups",
    )
    purge_result = manager.purge_report(repo_id)
    if purge_result.get("status") == "not_found":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository not found")
    if purge_result.get("status") != "purged":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=purge_result,
        )
    append_audit_event(
        request.app.state.storage,
        event_type="repository.purged",
        actor_id=user.user_id,
        repo_id=repo_id,
        target_type="repository",
        target_id=repository.id,
        metadata={"backup": backup_name, **preview},
    )
    return {"status": "purged", "id": repo_id, "backup": backup_name, **preview}


@router.post("/{repo_id}/dependencies")
async def add_dependency(
    request: Request,
    repo_id: str,
    dep: DependencyCreate,
    user: UserContext = Depends(get_current_user),
):
    """Add a dependency to a repository."""
    repo_mgr = AuthorizedRepositoryManager(request.app.state.storage, user)

    try:
        dep_type = DependencyType(dep.dependency_type)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid dependency_type: {dep.dependency_type}",
        )

    repo_mgr.require(repo_id)
    repo_mgr.require(dep.target_repo_id)

    dependency = RepositoryDependency(
        source_repo_id=repo_id,
        target_repo_id=dep.target_repo_id,
        dependency_type=dep_type,
        version=dep.version,
        notes=dep.notes,
    )

    try:
        result = repo_mgr.add_dependency(dependency)
    except NotImplementedError as e:
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    return {"id": result, "status": "created"}


@router.get("/{repo_id}/dependencies")
async def get_dependencies(
    request: Request,
    repo_id: str,
    user: UserContext = Depends(get_current_user),
):
    """Get repository dependencies."""
    repo_mgr = AuthorizedRepositoryManager(request.app.state.storage, user)
    repo_mgr.require(repo_id)
    deps = repo_mgr.get_dependencies(repo_id)

    return [
        {
            "target_repo_id": d.target_repo_id,
            "dependency_type": d.dependency_type.value,
            "version": d.version,
            "notes": d.notes,
        }
        for d in deps
    ]


@router.get("/{repo_id}/context")
async def get_cross_repo_context(
    request: Request,
    repo_id: str,
    include_deps: bool = True,
    environment: List[str] = Query(default=None),
    task_type: List[str] = Query(default=None),
    as_of: datetime = None,
    user: UserContext = Depends(get_current_user),
):
    """Get aggregated context from repo and dependencies."""
    storage = request.app.state.storage
    repo_mgr = AuthorizedRepositoryManager(storage, user)
    cross_mgr = CrossRepoContext(storage, repo_mgr=repo_mgr)

    try:
        context = cross_mgr.get_context_for_repo(
            repo_id=repo_id,
            include_dependencies=include_deps,
            environment=environment,
            task_type=task_type,
            as_of=as_of,
            memory_filter=lambda memory: can_access_scoped_record(
                storage, memory, user, scope_field="metadata"
            ),
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    if isinstance(context, dict) and "error" in context:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=context["error"])

    return context
