from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request, status

from llm_memory.server.auth import UserContext, get_current_user
from llm_memory.server.authorization import (
    can_access_scoped_record,
    require_repo_scope_access,
    require_scoped_record_access,
)
from llm_memory.server.schemas import RelationshipCreate

router = APIRouter(prefix="/relationships", tags=["relationships"])


@router.get("", response_model=List[dict])
async def list_relationships(
    request: Request,
    repo_id: str = None,
    user: UserContext = Depends(get_current_user),
):
    """Get all relationships."""
    storage = request.app.state.storage
    if not storage.get_capabilities().graph:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Storage backend does not support relationship listing",
        )
    require_repo_scope_access(storage, repo_id, user)
    relationships = storage.get_all_relationships(repo_id=repo_id)
    if user.is_admin:
        return relationships

    visible_relationships = []
    for relationship in relationships:
        source = storage.get_memory(relationship["source_id"])
        target = storage.get_memory(relationship["target_id"])
        if source and target and all(
            can_access_scoped_record(storage, memory, user, scope_field="metadata")
            for memory in (source, target)
        ):
            visible_relationships.append(relationship)
    return visible_relationships


@router.post("", response_model=dict)
async def add_relationship(
    request: Request,
    rel: RelationshipCreate,
    user: UserContext = Depends(get_current_user),
):
    """Add a relationship between memories."""
    storage = request.app.state.storage
    if not storage.get_capabilities().graph:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Storage backend does not support relationships",
        )
    source = require_scoped_record_access(
        storage,
        storage.get_memory(rel.source_id),
        user,
        scope_field="metadata",
        not_found_detail="Memory not found",
    )
    target = require_scoped_record_access(
        storage,
        storage.get_memory(rel.target_id),
        user,
        scope_field="metadata",
        not_found_detail="Memory not found",
    )
    if source.get("repo_id") != target.get("repo_id"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Memory relationships cannot cross repository boundaries",
        )
    try:
        evidence = rel.evidence.model_dump(exclude_none=True) if rel.evidence else None
        rel_id = storage.add_relationship(
            source_id=rel.source_id,
            target_id=rel.target_id,
            relationship=rel.relationship,
            strength=rel.strength,
            evidence=evidence,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return {"id": rel_id, "status": "created"}


@router.delete("/{relationship_id}")
async def delete_relationship(
    request: Request,
    relationship_id: str,
    user: UserContext = Depends(get_current_user),
):
    """Delete a relationship after verifying access to both endpoints."""
    storage = request.app.state.storage
    relationship = next(
        (
            item
            for item in storage.get_all_relationships()
            if item.get("id") == relationship_id
        ),
        None,
    )
    if not relationship:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Relationship not found")
    for memory_id in (relationship.get("source_id"), relationship.get("target_id")):
        require_scoped_record_access(
            storage,
            storage.get_memory(memory_id),
            user,
            scope_field="metadata",
            not_found_detail="Relationship not found",
        )
    if not storage.delete_relationship(relationship_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Relationship not found")
    return {"status": "deleted", "id": relationship_id}
