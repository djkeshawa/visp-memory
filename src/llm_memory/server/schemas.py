"""
Pydantic schemas for the Memory Server API.
"""

from pydantic import BaseModel
from typing import List, Dict, Any, Optional
from datetime import datetime

class MemoryCreate(BaseModel):
    content: str
    layer: str = "episodic"
    category: str = "note"
    importance: float = 0.5
    repo_id: Optional[str] = None
    tags: List[str] = []
    metadata: Dict[str, Any] = {}

class MemoryResponse(MemoryCreate):
    id: str
    created_at: datetime
    accessed_at: datetime

class SearchQuery(BaseModel):
    query: str
    layers: Optional[List[str]] = None
    repo_id: Optional[str] = None
    limit: int = 10

class IntentCreate(BaseModel):
    description: str
    priority: int = 1
    context: Dict[str, Any] = {}

class IntentResponse(IntentCreate):
    id: str
    status: str
    created_at: datetime

class MemoryUpdate(BaseModel):
    content: Optional[str] = None
    importance: Optional[float] = None
    tags: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None

class RelationshipCreate(BaseModel):
    source_id: str
    target_id: str
    relationship: str
    strength: float = 1.0
