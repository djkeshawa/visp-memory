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

class RepositoryCreate(BaseModel):
    name: str
    id: Optional[str] = None
    url: Optional[str] = None
    description: Optional[str] = None
    tech_stack: List[str] = []
    metadata: Dict[str, Any] = {}

class RepositoryResponse(RepositoryCreate):
    id: str
    created_at: datetime

class DependencyCreate(BaseModel):
    target_repo_id: str
    dependency_type: str = "depends_on"
    version: Optional[str] = None
    notes: Optional[str] = None

class UserCreate(BaseModel):
    username: str
    id: Optional[str] = None
    email: Optional[str] = None
    display_name: Optional[str] = None
    metadata: Dict[str, Any] = {}

class UserResponse(UserCreate):
    id: str
    created_at: datetime
    last_active: datetime

class TeamCreate(BaseModel):
    name: str
    id: Optional[str] = None
    description: Optional[str] = None
    metadata: Dict[str, Any] = {}

class TeamResponse(TeamCreate):
    id: str
    created_at: datetime

class MemberAdd(BaseModel):
    user_id: str
