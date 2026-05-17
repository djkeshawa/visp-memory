from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request

from llm_memory.config import load_config
from llm_memory.core.ranking import rank_memory_results
from llm_memory.server.auth import UserContext, get_current_user
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


@router.get("/memories", response_model=List[MemoryResponse])
async def list_memories(
    request: Request,
    repo_id: str = None,
    layer: str = None,
    category: str = None,
    limit: int = 50,
    order_by: str = "created_at DESC",
    user: UserContext = Depends(get_current_user),
):
    storage = request.app.state.storage
    config = load_config()
    limit = max(1, min(limit, 200))
    memories = storage.list_memories(
        limit=limit,
        repo_id=repo_id or config.repo_id,
        layer=layer,
        category=category,
        order_by=order_by,
    )
    return [
        {
            "id": m["id"],
            "content": m["content"],
            "layer": m["layer"],
            "category": m["category"],
            "repo_id": m.get("repo_id"),
            "importance": m.get("importance", 0.5),
            "tags": m.get("tags", []),
            "metadata": m.get("metadata", {}),
            "created_at": _as_datetime(m.get("created_at")),
            "accessed_at": _as_datetime(m.get("accessed_at")),
            "similarity": m.get("similarity"),
            "relevance_score": m.get("relevance_score"),
        }
        for m in memories
    ]


@router.post("/memories", response_model=MemoryResponse)
async def create_memory(
    request: Request, memory: MemoryCreate, user: UserContext = Depends(get_current_user)
):
    storage = request.app.state.storage
    config = load_config()

    # Add author attribution to metadata
    metadata = memory.metadata or {}
    metadata["author_id"] = user.user_id
    if user.team_id:
        metadata["team_id"] = user.team_id

    mem_id = storage.store_memory(
        content=memory.content,
        layer=memory.layer,
        category=memory.category,
        importance=memory.importance,
        repo_id=memory.repo_id or config.repo_id,
        tags=memory.tags,
        metadata=metadata,
    )
    return {
        "id": mem_id,
        **memory.model_dump(),
        "metadata": metadata,
        "repo_id": memory.repo_id or config.repo_id,
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
    if not mem:
        raise HTTPException(status_code=404, detail="Memory not found")

    return {
        "id": mem["id"],
        "content": mem["content"],
        "layer": mem["layer"],
        "category": mem["category"],
        "repo_id": mem.get("repo_id"),
        "importance": mem.get("importance", 0.5),
        "tags": mem.get("tags", []),
        "metadata": mem.get("metadata", {}),
        "created_at": _as_datetime(mem.get("created_at")),
        "accessed_at": _as_datetime(mem.get("accessed_at")),
        "similarity": mem.get("similarity"),
        "relevance_score": mem.get("relevance_score"),
    }


@router.delete("/memories/{memory_id}")
async def delete_memory(
    request: Request, memory_id: str, user: UserContext = Depends(get_current_user)
):
    storage = request.app.state.storage
    success = storage.delete_memory(memory_id)
    if not success:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"status": "deleted", "id": memory_id}


@router.patch("/memories/{memory_id}")
async def update_memory(
    request: Request,
    memory_id: str,
    update: MemoryUpdate,
    user: UserContext = Depends(get_current_user),
):
    storage = request.app.state.storage

    # Filter out None values
    update_data = {k: v for k, v in update.model_dump().items() if v is not None}
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")

    success = storage.update_memory(memory_id, **update_data)
    if not success:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"status": "updated", "id": memory_id}


@router.post("/recall", response_model=List[MemoryResponse])
async def recall(
    request: Request, query: SearchQuery, user: UserContext = Depends(get_current_user)
):
    storage = request.app.state.storage
    config = load_config()
    layers = query.layers or [None]
    results = []
    for layer in layers:
        results.extend(
            storage.search_memories(
                query=query.query,
                layer=layer,
                limit=query.limit,
                repo_id=query.repo_id or config.repo_id,
            )
        )
    results = rank_memory_results(results, query=query.query, limit=query.limit)
    return [
        {
            "id": r["id"],
            "content": r["content"],
            "layer": r["layer"],
            "category": r["category"],
            "repo_id": r.get("repo_id"),
            "importance": r.get("importance", 0.5),
            "tags": r.get("tags", []),
            "metadata": r.get("metadata", {}),
            "created_at": _as_datetime(r.get("created_at")),
            "accessed_at": _as_datetime(r.get("accessed_at")),
            "similarity": r.get("similarity"),
            "relevance_score": r.get("relevance_score"),
        }
        for r in results
    ]


@router.get("/graph")
async def get_graph_data(
    request: Request, repo_id: str = None, user: UserContext = Depends(get_current_user)
):
    """Get memory graph (nodes and edges)."""
    storage = request.app.state.storage
    config = load_config()
    graph_repo_id = repo_id or config.repo_id
    memories = storage.list_memories(limit=200, repo_id=graph_repo_id)
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
            }
            for r in relationships
        ],
    }
