from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from llm_memory.server.auth import UserContext, get_current_user

router = APIRouter(prefix="/relationships", tags=["relationships"])


class RelationshipCreate(BaseModel):
    source_id: str
    target_id: str
    relationship: str
    strength: float = 1.0


@router.get("", response_model=List[dict])
async def list_relationships(
    request: Request,
    repo_id: str = None,
    user: UserContext = Depends(get_current_user),
):
    """Get all relationships."""
    storage = request.app.state.storage
    if not hasattr(storage, "get_all_relationships"):
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Storage backend does not support relationship listing",
        )
    return storage.get_all_relationships(repo_id=repo_id)


@router.post("", response_model=dict)
async def add_relationship(
    request: Request,
    rel: RelationshipCreate,
    user: UserContext = Depends(get_current_user),
):
    """Add a relationship between memories."""
    storage = request.app.state.storage
    if not hasattr(storage, "add_relationship"):
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Storage backend does not support relationships",
        )
    try:
        rel_id = storage.add_relationship(
            source_id=rel.source_id,
            target_id=rel.target_id,
            relationship=rel.relationship,
            strength=rel.strength,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return {"id": rel_id}
