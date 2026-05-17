"""
Pydantic schemas for the Memory Server API.
"""

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

MemoryLayer = Literal["raw", "episodic", "semantic", "intent"]
MAX_QUERY_LIMIT = 200


class MemoryCreate(BaseModel):
    content: str = Field(min_length=1)
    layer: MemoryLayer = "episodic"
    category: str = "note"
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    repo_id: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    source_ids: List[str] = Field(default_factory=list)


class MemoryResponse(BaseModel):
    id: str
    content: str
    layer: str
    category: str
    importance: float = 0.5
    repo_id: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    accessed_at: datetime
    similarity: Optional[float] = None
    relevance_score: Optional[float] = None


class SearchQuery(BaseModel):
    query: str = Field(min_length=1)
    layers: Optional[List[MemoryLayer]] = None
    repo_id: Optional[str] = None
    limit: int = Field(default=10, ge=1, le=MAX_QUERY_LIMIT)


class IntentCreate(BaseModel):
    description: str
    priority: int = 1
    repo_id: Optional[str] = None
    context: Dict[str, Any] = Field(default_factory=dict)


class IntentResponse(IntentCreate):
    id: str
    status: str
    created_at: datetime


class MemoryUpdate(BaseModel):
    content: Optional[str] = Field(default=None, min_length=1)
    importance: Optional[float] = Field(default=None, ge=0.0, le=1.0)
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
    tech_stack: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


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
    metadata: Dict[str, Any] = Field(default_factory=dict)


class UserResponse(UserCreate):
    id: str
    created_at: datetime
    last_active: datetime


class TeamCreate(BaseModel):
    name: str
    id: Optional[str] = None
    description: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class TeamResponse(TeamCreate):
    id: str
    created_at: datetime


class MemberAdd(BaseModel):
    user_id: str
