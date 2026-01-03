"""
Pydantic schemas for the Memory Server API.
"""

from pydantic import BaseModel, Field
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
