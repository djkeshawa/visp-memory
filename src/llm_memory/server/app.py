"""
FastAPI Server Entry Point
"""

try:
    from fastapi import FastAPI, Depends, HTTPException, Security
    from fastapi.security.api_key import APIKeyHeader
except ImportError:
    raise ImportError("FastAPI not installed. Run: pip install llm-memory[api]")

from llm_memory.server.schemas import MemoryCreate, MemoryResponse, SearchQuery, IntentCreate, IntentResponse
from typing import List

from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime
from llm_memory.core.storage import LocalStorage
from llm_memory.config import load_config

config = load_config()
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
async def list_memories(api_key: str = Depends(get_api_key)):
    memories = storage.list_memories(limit=50) # Default limit
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
        repo_id=memory.repo_id,
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
        repo_id=query.repo_id
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
async def list_intents(api_key: str = Depends(get_api_key)):
    # Placeholder for intents - Storage doesn't explicitly expose list_intents yet, 
    # but we can filter memories by layer='intent'
    intents = storage.list_memories(limit=100)
    intents = [i for i in intents if i['layer'] == 'intent']
    
    return [
        {
            "id": i["id"],
            "description": i["content"],
            "priority": i.get("importance", 1) * 10, # Map 0.1-1.0 to 1-10 roughly
            "context": i.get("metadata", {}),
            "status": i.get("metadata", {}).get("status", "active"),
            "created_at": datetime.fromisoformat(i["created_at"]) if isinstance(i["created_at"], str) else i["created_at"]
        } for i in intents
    ]
