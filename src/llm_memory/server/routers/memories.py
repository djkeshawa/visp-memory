from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request

from llm_memory.config import load_config
from llm_memory.core.ranking import rank_memory_results
from llm_memory.server.auth import UserContext, get_current_user
from llm_memory.server.authorization import (
    can_access_scoped_record,
    require_repo_scope_access,
    require_scoped_record_access,
)
from llm_memory.server.routers.platform import append_audit_event
from llm_memory.server.schemas import (
    MemoryCreate,
    MemoryResponse,
    MemoryUpdate,
    SearchQuery,
)

# No prefix to maintain backward compatibility for /recall and /relationships
router = APIRouter(tags=["memories"])


def _as_datetime(value):
    """Normalize storage timestamps for API responses."""
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    return value or datetime.now()


def _as_optional_datetime(value):
    if value is None:
        return None
    return _as_datetime(value)


def _memory_response_payload(memory: dict):
    return {
        "id": memory["id"],
        "content": memory["content"],
        "layer": memory["layer"],
        "category": memory["category"],
        "repo_id": memory.get("repo_id"),
        "importance": memory.get("importance", 0.5),
        "tags": memory.get("tags", []),
        "metadata": memory.get("metadata", {}),
        "status": memory.get("status", "active"),
        "source": memory.get("source"),
        "quality_flags": memory.get("quality_flags", []),
        "approved_by": memory.get("approved_by"),
        "approved_at": _as_optional_datetime(memory.get("approved_at")),
        "archived_at": _as_optional_datetime(memory.get("archived_at")),
        "created_at": _as_datetime(memory.get("created_at")),
        "accessed_at": _as_datetime(memory.get("accessed_at")),
        "similarity": memory.get("similarity"),
        "relevance_score": memory.get("relevance_score"),
    }


def _latest_visible_memory(
    storage,
    *,
    repo_id: str,
    user: UserContext,
    layer: str = None,
    category: str = None,
    status: str = "active",
):
    memories = storage.list_memories(
        limit=50,
        repo_id=repo_id,
        layer=layer,
        category=category,
        status=status,
        order_by="created_at DESC",
    )
    return next(
        (
            memory
            for memory in memories
            if can_access_scoped_record(storage, memory, user, scope_field="metadata")
        ),
        None,
    )


@router.get("/remember", response_model=MemoryResponse)
async def remember_latest_memory(
    request: Request,
    repo_id: str = None,
    layer: str = None,
    category: str = None,
    status: str = "active",
    user: UserContext = Depends(get_current_user),
):
    """Recall the newest visible memory for the current repository scope."""
    storage = request.app.state.storage
    config = load_config()
    memory_repo_id = repo_id or config.repo_id
    require_repo_scope_access(storage, memory_repo_id, user)
    latest = _latest_visible_memory(
        storage,
        repo_id=memory_repo_id,
        user=user,
        layer=layer,
        category=category,
        status=status,
    )
    if latest is None:
        raise HTTPException(status_code=404, detail="No memories found")
    return _memory_response_payload(latest)


@router.get("/memories", response_model=List[MemoryResponse])
async def list_memories(
    request: Request,
    repo_id: str = None,
    layer: str = None,
    category: str = None,
    status: str = "active",
    limit: int = 50,
    order_by: str = "created_at DESC",
    user: UserContext = Depends(get_current_user),
):
    storage = request.app.state.storage
    config = load_config()
    memory_repo_id = repo_id or config.repo_id
    require_repo_scope_access(storage, memory_repo_id, user)
    limit = max(1, min(limit, 200))
    memories = storage.list_memories(
        limit=limit,
        repo_id=memory_repo_id,
        layer=layer,
        category=category,
        status=status,
        order_by=order_by,
    )
    memories = [
        m for m in memories if can_access_scoped_record(storage, m, user, scope_field="metadata")
    ]
    return [_memory_response_payload(m) for m in memories]


@router.post("/memories", response_model=MemoryResponse)
async def create_memory(
    request: Request, memory: MemoryCreate, user: UserContext = Depends(get_current_user)
):
    storage = request.app.state.storage
    config = load_config()
    memory_repo_id = memory.repo_id or config.repo_id
    require_repo_scope_access(storage, memory_repo_id, user)

    # Add author attribution to metadata
    metadata = dict(memory.metadata or {})
    metadata["author_id"] = user.user_id
    if user.team_id:
        metadata["team_id"] = user.team_id

    mem_id = storage.store_memory(
        content=memory.content,
        layer=memory.layer,
        category=memory.category,
        importance=memory.importance,
        repo_id=memory_repo_id,
        tags=memory.tags,
        metadata=metadata,
        source_ids=memory.source_ids,
        status=memory.status,
        source=memory.source,
        quality_flags=memory.quality_flags,
    )
    append_audit_event(
        storage,
        event_type="memory.created",
        actor_id=user.user_id,
        repo_id=memory_repo_id,
        target_type="memory",
        target_id=mem_id,
        metadata={"layer": memory.layer, "category": memory.category},
    )
    return {
        "id": mem_id,
        **memory.model_dump(),
        "metadata": metadata,
        "repo_id": memory_repo_id,
        "created_at": datetime.now(),
        "accessed_at": datetime.now(),
        "similarity": None,
        "relevance_score": None,
    }


@router.get("/memories/{memory_id}", response_model=MemoryResponse)
async def get_memory(
    request: Request, memory_id: str, user: UserContext = Depends(get_current_user)
):
    storage = request.app.state.storage
    mem = storage.get_memory(memory_id)
    mem = require_scoped_record_access(
        storage, mem, user, scope_field="metadata", not_found_detail="Memory not found"
    )

    return _memory_response_payload(mem)


@router.delete("/memories/{memory_id}")
async def delete_memory(
    request: Request, memory_id: str, user: UserContext = Depends(get_current_user)
):
    storage = request.app.state.storage
    mem = storage.get_memory(memory_id)
    require_scoped_record_access(
        storage, mem, user, scope_field="metadata", not_found_detail="Memory not found"
    )
    success = storage.delete_memory(memory_id)
    if not success:
        raise HTTPException(status_code=404, detail="Memory not found")
    append_audit_event(
        storage,
        event_type="memory.deleted",
        actor_id=user.user_id,
        repo_id=mem.get("repo_id"),
        target_type="memory",
        target_id=memory_id,
    )
    return {"status": "deleted", "id": memory_id}


@router.patch("/memories/{memory_id}")
async def update_memory(
    request: Request,
    memory_id: str,
    update: MemoryUpdate,
    user: UserContext = Depends(get_current_user),
):
    storage = request.app.state.storage
    mem = storage.get_memory(memory_id)
    mem = require_scoped_record_access(
        storage, mem, user, scope_field="metadata", not_found_detail="Memory not found"
    )

    # Filter out None values
    update_data = {k: v for k, v in update.model_dump().items() if v is not None}
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")
    if "metadata" in update_data and not user.is_admin:
        metadata = dict(update_data["metadata"] or {})
        existing_metadata = mem.get("metadata") or {}
        for reserved_key in ("author_id", "team_id"):
            if reserved_key in existing_metadata:
                metadata[reserved_key] = existing_metadata[reserved_key]
        update_data["metadata"] = metadata
    if update_data.get("status") == "active" and mem.get("status") == "pending":
        update_data["approved_by"] = user.user_id
        update_data["approved_at"] = datetime.now().isoformat()

    success = storage.update_memory(memory_id, **update_data)
    if not success:
        raise HTTPException(status_code=404, detail="Memory not found")
    event_type = "memory.archived" if update_data.get("status") == "archived" else "memory.updated"
    append_audit_event(
        storage,
        event_type=event_type,
        actor_id=user.user_id,
        repo_id=mem.get("repo_id"),
        target_type="memory",
        target_id=memory_id,
        metadata={"fields": sorted(update_data)},
    )
    return {"status": "updated", "id": memory_id}


@router.post("/recall", response_model=List[MemoryResponse])
async def recall(
    request: Request, query: SearchQuery, user: UserContext = Depends(get_current_user)
):
    storage = request.app.state.storage
    config = load_config()
    recall_repo_id = query.repo_id or config.repo_id
    require_repo_scope_access(storage, recall_repo_id, user)
    layers = query.layers or [None]
    results = []
    for layer in layers:
        layer_results = storage.search_memories(
            query=query.query,
            layer=layer,
            limit=query.limit,
            repo_id=recall_repo_id,
            status=query.status,
        )
        results.extend(
            r
            for r in layer_results
            if can_access_scoped_record(storage, r, user, scope_field="metadata")
        )
    results = rank_memory_results(
        results, query=query.query, limit=query.limit, min_score=query.min_score
    )
    return [_memory_response_payload(r) for r in results]


@router.get("/graph")
async def get_graph_data(
    request: Request, repo_id: str = None, user: UserContext = Depends(get_current_user)
):
    """Get memory graph (nodes and edges)."""
    storage = request.app.state.storage
    config = load_config()
    graph_repo_id = repo_id or config.repo_id
    require_repo_scope_access(storage, graph_repo_id, user)
    memories = storage.list_memories(limit=200, repo_id=graph_repo_id)
    memories = [
        m for m in memories if can_access_scoped_record(storage, m, user, scope_field="metadata")
    ]
    visible_memory_ids = {m["id"] for m in memories}
    relationships = storage.get_all_relationships(repo_id=graph_repo_id)

    return {
        "nodes": [
            {
                "id": m["id"],
                "group": m["layer"],
                "label": m["content"][:30] + "..." if len(m["content"]) > 30 else m["content"],
                "full_label": m["content"],
                "radius": 5 + (m.get("importance", 0.5) * 5),
                "layer": m["layer"],
            }
            for m in memories
        ],
        "links": [
            {
                "source": r["source_id"],
                "target": r["target_id"],
                "value": r["strength"],
                "label": r["relationship"],
                "evidence": r.get("evidence"),
            }
            for r in relationships
            if r["source_id"] in visible_memory_ids and r["target_id"] in visible_memory_ids
        ],
    }
