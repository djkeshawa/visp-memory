from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status

from llm_memory.core.cross_repo import CrossRepoContext
from llm_memory.core.repository import (
    DependencyType,
    Repository,
    RepositoryDependency,
    RepositoryManager,
)
from llm_memory.server.auth import UserContext, get_current_user
from llm_memory.server.schemas import DependencyCreate, RepositoryCreate, RepositoryResponse

router = APIRouter(prefix="/repos", tags=["repositories"])


@router.post("", response_model=RepositoryResponse)
async def register_repository(
    request: Request,
    repo: RepositoryCreate,
    user: UserContext = Depends(get_current_user),
):
    """Register a new repository."""
    repo_mgr = RepositoryManager(request.app.state.storage)

    repo_obj = Repository(
        id=repo.id or repo.name.lower().replace(" ", "-"),
        name=repo.name,
        url=repo.url,
        description=repo.description,
        tech_stack=repo.tech_stack or [],
        team_id=user.team_id,
        metadata=repo.metadata or {},
    )

    try:
        repo_id = repo_mgr.register(repo_obj)
    except NotImplementedError as e:
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

    return {
        **repo_obj.__dict__,
        "id": repo_id,
        "created_at": datetime.now(),
    }


@router.get("", response_model=List[RepositoryResponse])
async def list_repositories(
    request: Request,
    team_id: Optional[str] = None,
    user: UserContext = Depends(get_current_user),
):
    """List all repositories."""
    repo_mgr = RepositoryManager(request.app.state.storage)
    repos = repo_mgr.list_all(team_id=team_id or user.team_id)

    return [
        {
            **r.__dict__,
            "created_at": r.created_at or datetime.now(),
        }
        for r in repos
    ]


@router.get("/{repo_id}", response_model=RepositoryResponse)
async def get_repository(
    request: Request,
    repo_id: str,
    user: UserContext = Depends(get_current_user),
):
    """Get repository details."""
    repo_mgr = RepositoryManager(request.app.state.storage)
    repo = repo_mgr.get(repo_id)
    if not repo:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository not found")

    return {
        **repo.__dict__,
        "created_at": repo.created_at or datetime.now(),
    }


@router.post("/{repo_id}/dependencies")
async def add_dependency(
    request: Request,
    repo_id: str,
    dep: DependencyCreate,
    user: UserContext = Depends(get_current_user),
):
    """Add a dependency to a repository."""
    repo_mgr = RepositoryManager(request.app.state.storage)

    try:
        dep_type = DependencyType(dep.dependency_type)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid dependency_type: {dep.dependency_type}",
        )

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
    repo_mgr = RepositoryManager(request.app.state.storage)
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
    user: UserContext = Depends(get_current_user),
):
    """Get aggregated context from repo and dependencies."""
    storage = request.app.state.storage
    cross_mgr = CrossRepoContext(storage)

    context = cross_mgr.get_context_for_repo(
        repo_id=repo_id,
        include_dependencies=include_deps,
    )

    if isinstance(context, dict) and "error" in context:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=context["error"])

    return context
