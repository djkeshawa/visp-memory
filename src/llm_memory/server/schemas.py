"""
Pydantic schemas for the Memory Server API.
"""

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from llm_memory.core.ranking import DEFAULT_RECALL_MIN_SCORE

MemoryLayer = Literal["raw", "episodic", "semantic", "intent"]
MemoryStatus = Literal["active", "pending", "archived", "superseded", "deleted"]
IntentStatus = Literal["active", "completed", "closed"]
RelationshipConfidence = Literal["observed", "inferred", "ambiguous", "manual"]
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
    status: MemoryStatus = "active"
    source: Optional[str] = None
    quality_flags: List[str] = Field(default_factory=list)


class MemoryResponse(BaseModel):
    id: str
    content: str
    layer: str
    category: str
    importance: float = 0.5
    repo_id: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    status: MemoryStatus = "active"
    source: Optional[str] = None
    quality_flags: List[str] = Field(default_factory=list)
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    archived_at: Optional[datetime] = None
    created_at: datetime
    accessed_at: datetime
    similarity: Optional[float] = None
    relevance_score: Optional[float] = None


class SearchQuery(BaseModel):
    query: str = Field(min_length=1)
    layers: Optional[List[MemoryLayer]] = None
    repo_id: Optional[str] = None
    status: MemoryStatus = "active"
    limit: int = Field(default=10, ge=1, le=MAX_QUERY_LIMIT)
    min_score: float = Field(default=DEFAULT_RECALL_MIN_SCORE, ge=0.0, le=1.0)


GraphRecallMode = Literal["neighbors", "path", "trace", "why_relevant"]


class RelationshipEvidence(BaseModel):
    confidence: RelationshipConfidence = "ambiguous"
    confidence_score: float = Field(default=0.5, ge=0.0, le=1.0)
    source: Optional[str] = None
    source_file: Optional[str] = None
    source_location: Optional[str] = None
    reason: Optional[str] = None
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None


class GraphTraceRequest(BaseModel):
    query: str = Field(min_length=1)
    repo_id: Optional[str] = None
    depth: int = Field(default=2, ge=0, le=4)
    token_budget: int = Field(default=2000, ge=1, le=100000)
    limit: int = Field(default=5, ge=1, le=50)
    relationship_filter: Optional[str] = None


class GraphNeighborsRequest(BaseModel):
    memory_id: str = Field(min_length=1)
    repo_id: Optional[str] = None
    relationship_filter: Optional[str] = None
    depth: int = Field(default=1, ge=0, le=4)
    token_budget: int = Field(default=2000, ge=1, le=100000)
    limit: int = Field(default=25, ge=1, le=100)


class GraphPathRequest(BaseModel):
    source_id: str = Field(min_length=1)
    target_id: str = Field(min_length=1)
    repo_id: Optional[str] = None
    max_hops: int = Field(default=4, ge=1, le=6)
    token_budget: int = Field(default=2000, ge=1, le=100000)


class GraphWhyRelevantRequest(BaseModel):
    query: str = Field(min_length=1)
    memory_id: str = Field(min_length=1)
    repo_id: Optional[str] = None
    depth: int = Field(default=2, ge=0, le=4)
    token_budget: int = Field(default=2000, ge=1, le=100000)
    limit: int = Field(default=5, ge=1, le=50)


class GraphRecallNode(BaseModel):
    id: str
    content: str
    layer: str
    category: Optional[str] = None
    importance: float
    repo_id: Optional[str] = None
    relevance_score: float
    relevance_factors: Dict[str, Any] = Field(default_factory=dict)


class GraphRecallEdge(BaseModel):
    id: str
    source_id: str
    target_id: str
    relationship: str
    strength: float
    evidence: Optional[RelationshipEvidence] = None
    reason: str
    relevance_score: float
    relevance_factors: Dict[str, Any] = Field(default_factory=dict)


class GraphRecallOmission(BaseModel):
    type: str
    count: int = 1
    reason: str


class GraphRecallResponse(BaseModel):
    mode: GraphRecallMode
    query: Optional[str] = None
    nodes: List[GraphRecallNode]
    edges: List[GraphRecallEdge]
    omitted: List[GraphRecallOmission] = Field(default_factory=list)
    limits: Dict[str, int]
    explanation: str


ReportItemType = Literal["memory", "relationship", "intent", "question"]
ReportKind = Literal["stored_fact", "inferred_recommendation"]


class MemoryIntelligenceReportItem(BaseModel):
    type: ReportItemType
    id: str
    title: str
    reason: str
    facts: Dict[str, Any] = Field(default_factory=dict)


class MemoryIntelligenceReportSection(BaseModel):
    key: str
    title: str
    kind: ReportKind
    thresholds: Dict[str, Any] = Field(default_factory=dict)
    items: List[MemoryIntelligenceReportItem] = Field(default_factory=list)


class MemoryIntelligenceReportResponse(BaseModel):
    schema_version: str
    repo_id: Optional[str] = None
    as_of: Optional[datetime] = None
    thresholds: Dict[str, Any] = Field(default_factory=dict)
    summary: Dict[str, int] = Field(default_factory=dict)
    sections: Dict[str, MemoryIntelligenceReportSection]


class IntentCreate(BaseModel):
    description: str
    priority: int = 1
    repo_id: Optional[str] = None
    context: Dict[str, Any] = Field(default_factory=dict)


class IntentUpdate(BaseModel):
    description: Optional[str] = Field(default=None, min_length=1)
    priority: Optional[int] = None
    status: Optional[IntentStatus] = None
    context: Optional[Dict[str, Any]] = None


class IntentResponse(IntentCreate):
    id: str
    status: IntentStatus
    created_at: datetime
    updated_at: Optional[datetime] = None


class DecayPreviewItem(BaseModel):
    memory_id: str
    snippet: str
    layer: str
    category: Optional[str] = None
    repo_id: Optional[str] = None
    current_importance: float
    projected_importance: float
    decay_amount: float
    age_days: float
    access_count: int = 0
    last_accessed_at: Optional[datetime] = None
    created_at: datetime
    risk: Literal["stable", "weakening", "likely_to_decay", "at_floor"]
    reason: str


class DecayPreviewResponse(BaseModel):
    halflife_days: int
    min_importance: float
    decay_enabled: bool
    candidates: List[DecayPreviewItem]


class MemoryUpdate(BaseModel):
    content: Optional[str] = Field(default=None, min_length=1)
    importance: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    tags: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None
    status: Optional[MemoryStatus] = None
    source: Optional[str] = None
    quality_flags: Optional[List[str]] = None


class DuplicateCandidate(BaseModel):
    ids: List[str]
    contents: List[str]
    repo_id: Optional[str] = None
    layer: str
    category: Optional[str] = None
    similarity: float = 1.0
    reason: str


class QualityDuplicateResponse(BaseModel):
    candidates: List[DuplicateCandidate]


class AskMemoryRequest(BaseModel):
    query: str = Field(min_length=1)
    repo_id: Optional[str] = None
    layers: Optional[List[MemoryLayer]] = None
    category: Optional[str] = None
    limit: int = Field(default=5, ge=1, le=20)
    require_citations: bool = True


class AskMemoryCitation(BaseModel):
    memory_id: str
    snippet: str
    layer: str
    category: Optional[str] = None
    repo_id: Optional[str] = None
    relevance_score: Optional[float] = None


class AskMemoryResponse(BaseModel):
    answer: str
    citations: List[AskMemoryCitation]
    mode: Literal["retrieval_only", "generated"] = "retrieval_only"
    provider_status: Literal["not_configured", "available", "failed"] = "not_configured"


class AuditLogEntry(BaseModel):
    id: str
    event_type: str
    actor_id: Optional[str] = None
    repo_id: Optional[str] = None
    target_type: Optional[str] = None
    target_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class RelationshipCreate(BaseModel):
    source_id: str
    target_id: str
    relationship: str
    strength: float = 1.0
    evidence: Optional[RelationshipEvidence] = None


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


class ProjectScopeResponse(BaseModel):
    id: str
    name: str
    registered: bool = False


ProviderStatusValue = Literal[
    "connected",
    "failed",
    "disabled",
    "fallback",
    "not_configured",
    "not_checked",
]


class ProviderStatus(BaseModel):
    provider: str
    configured: bool
    selected: bool = False
    active: bool = False
    connected: bool = False
    status: ProviderStatusValue
    model: Optional[str] = None
    dimension: Optional[int] = None
    last_checked_at: Optional[datetime] = None
    error_code: Optional[str] = None
    message: str
    action_hint: Optional[str] = None


class ProviderDiagnosticsResponse(BaseModel):
    active_provider: str
    effective_provider: Optional[str] = None
    providers: List[ProviderStatus]


class EmbeddingIndexStatus(BaseModel):
    storage_backend: str
    provider: str
    effective_provider: Optional[str] = None
    model: Optional[str] = None
    dimension: Optional[int] = None
    status: Literal["available", "disabled", "not_configured", "unknown"]
    message: str
    scope: Dict[str, str] = Field(default_factory=dict)
    matched_memories: int = 0
    indexed_memories: Optional[int] = None
    active_collections: List[str] = Field(default_factory=list)
    legacy_collections: List[str] = Field(default_factory=list)
    needs_reindex: bool = False


class EmbeddingReindexRequest(BaseModel):
    repo_id: Optional[str] = None
    layer: Optional[MemoryLayer] = None
    category: Optional[str] = None
    dry_run: bool = True


class EmbeddingReindexResponse(BaseModel):
    dry_run: bool
    status: Literal[
        "ready",
        "completed",
        "partial_failure",
        "disabled",
        "not_configured",
        "unsupported",
    ]
    message: str
    scope: Dict[str, str] = Field(default_factory=dict)
    matched_memories: int
    reindexed_memories: int = 0
    failed_memories: int = 0
    dimension: Optional[int] = None
    active_collections: List[str] = Field(default_factory=list)
    legacy_collections: List[str] = Field(default_factory=list)
    errors: List[Dict[str, str]] = Field(default_factory=list)


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
