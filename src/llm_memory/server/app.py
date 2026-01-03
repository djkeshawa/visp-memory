"""
FastAPI Server Entry Point
"""

try:
    from fastapi import FastAPI, Depends, HTTPException, Security
    from fastapi.security.api_key import APIKeyHeader
except ImportError:
    raise ImportError("FastAPI not installed. Run: pip install llm-memory[api]")

from llm_memory.server.schemas import (
    MemoryCreate, 
    MemoryResponse, 
    SearchQuery, 
    IntentCreate, 
    IntentResponse,
    MemoryUpdate,
    RelationshipCreate
)
from typing import List

from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime
from llm_memory.core.storage import LocalStorage
from llm_memory.core.neo4j_storage import Neo4jStorage
from llm_memory.config import load_config
import logging

logger = logging.getLogger(__name__)

config = load_config()

if config.storage.backend == "neo4j":
    try:
        storage = Neo4jStorage()
        logger.info("Initialized Neo4j Storage")
    except Exception as e:
        logger.error(f"Failed to initialize Neo4j, falling back to SQLite: {e}")
        storage = LocalStorage(config.storage.data_dir)
else:
    storage = LocalStorage(config.storage.data_dir)

# In real implementation, this would connect to the Storage backend
# For now, it's a skeleton

app = FastAPI(
    title="LLM Central Memory Server",
    description="Shared memory server for multi-repo context",
    version="0.1.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

API_KEY_NAME = "X-API-Key"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)

async def get_api_key(api_key_header: str = Security(api_key_header)):
    """Simple API Key Auth"""
    # In real implementation, validate against config/env
    if api_key_header:
        return api_key_header
    # Allow no auth for dev skeleton, or raise error
    # raise HTTPException(status_code=403, detail="Could not validate credentials")
    return None

@app.get("/")
async def root():
    stats = storage.get_stats()
    return stats

@app.get("/memories", response_model=List[MemoryResponse])
async def list_memories(repo_id: str = None, api_key: str = Depends(get_api_key)):
    memories = storage.list_memories(limit=50, repo_id=repo_id or config.repo_id) # Default limit
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
            "created_at": datetime.fromisoformat(m["created_at"]) if isinstance(m["created_at"], str) else m["created_at"],
            "accessed_at": datetime.now()
        } for m in memories
    ]

@app.post("/memories", response_model=MemoryResponse)
async def create_memory(memory: MemoryCreate, api_key: str = Depends(get_api_key)):
    mem_id = storage.store_memory(
        content=memory.content,
        layer=memory.layer,
        category=memory.category,
        importance=memory.importance,
        importance=memory.importance,
        repo_id=memory.repo_id or config.repo_id,
        tags=memory.tags,
        tags=memory.tags,
        metadata=memory.metadata
    )
    return {
        "id": mem_id,
        **memory.model_dump(),
        "created_at": datetime.now(),
        "accessed_at": datetime.now()
    }

@app.post("/recall", response_model=List[MemoryResponse])
async def recall(query: SearchQuery, api_key: str = Depends(get_api_key)):
    results = storage.search_memories(
        query=query.query,
        limit=query.limit,
        limit=query.limit,
        repo_id=query.repo_id or config.repo_id
    )
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
            "created_at": datetime.fromisoformat(r["created_at"]) if isinstance(r["created_at"], str) else r["created_at"],
            "accessed_at": datetime.now()
        } for r in results
    ]

@app.get("/intents", response_model=List[IntentResponse])
async def list_intents(repo_id: str = None, api_key: str = Depends(get_api_key)):
    # Use the dedicated storage method to fetch active intents
    intents = storage.get_active_intents(repo_id=repo_id or config.repo_id)
    
    return [
        {
            "id": i["id"],
            "description": i["description"],
            "priority": i["priority"],  
            "context": i.get("context", {}),
            "status": i["status"],
            "created_at": i["created_at"]
        } for i in intents
    ]

@app.get("/graph")
async def get_graph(repo_id: str = None, api_key: str = Depends(get_api_key)):
    """Get memory graph (nodes and edges)."""
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
                "layer": m["layer"]
            } for m in memories
        ],
        "links": [
            {
                "source": r["source_id"],
                "target": r["target_id"],
                "value": r["strength"],
                "label": r["relationship"]
            } for r in relationships
        ]
    }

@app.post("/intents", response_model=IntentResponse)
async def create_intent(intent: IntentCreate, api_key: str = Depends(get_api_key)):
    """Create a new intent."""
    intent_id = storage.set_intent(
        description=intent.description,
        priority=intent.priority if hasattr(intent, "priority") else 0,
        context=intent.context
    )
    
    return {
        "id": intent_id,
        "description": intent.description,
        "priority": intent.priority * 10,
        "status": "active",
        "context": intent.context,
        "created_at": datetime.now()
    }

@app.get("/memories/{memory_id}", response_model=MemoryResponse)
async def get_memory(memory_id: str, api_key: str = Depends(get_api_key)):
    """Get a single memory by ID."""
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
        "created_at": datetime.fromisoformat(mem["created_at"]) if isinstance(mem["created_at"], str) else mem["created_at"],
        "accessed_at": datetime.now()
    }

@app.delete("/memories/{memory_id}")
async def delete_memory(memory_id: str, api_key: str = Depends(get_api_key)):
    """Delete a memory."""
    success = storage.delete_memory(memory_id)
    if not success:
         raise HTTPException(status_code=404, detail="Memory not found")
    return {"status": "deleted", "id": memory_id}

@app.patch("/memories/{memory_id}")
async def update_memory(memory_id: str, update: MemoryUpdate, api_key: str = Depends(get_api_key)):
    """Update a memory."""
    # Filter out None values
    update_data = {k: v for k, v in update.model_dump().items() if v is not None}
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")
        
    success = storage.update_memory(memory_id, **update_data)
    if not success:
         raise HTTPException(status_code=404, detail="Memory not found")
    return {"status": "updated", "id": memory_id}

@app.post("/relationships")
async def create_relationship(rel: RelationshipCreate, api_key: str = Depends(get_api_key)):
    """Create a relationship between memories."""
    rel_id = storage.add_relationship(
        source_id=rel.source_id,
        target_id=rel.target_id,
        relationship=rel.relationship,
        strength=rel.strength
    )
    return {"id": rel_id, "status": "created"}
