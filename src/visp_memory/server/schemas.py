"""
Pydantic schemas for the Memory Server API.
"""

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from visp_memory.core.beliefs import (
    BeliefType,
    EpistemicStatus,
    normalize_belief_type,
)
from visp_memory.core.eligibility import normalize_scope_values
from visp_memory.core.ranking import DEFAULT_RECALL_MIN_SCORE

MemoryLayer = Literal["raw", "episodic", "semantic", "intent"]
MemoryStatus = Literal["active", "pending", "archived", "superseded", "merged", "deleted"]
IntentStatus = Literal["active", "completed", "closed"]
RelationshipConfidence = Literal["observed", "inferred", "ambiguous", "manual"]
MAX_QUERY_LIMIT = 200


class ScopedRequest(BaseModel):
    """Normalize optional environment and task constraints at the API boundary."""

    environment: Optional[List[str]] = None
    task_type: Optional[List[str]] = None

    @field_validator("environment", "task_type", mode="before")
    @classmethod
    def normalize_scope(cls, value, info):
        if value is None:
            return None
        return list(normalize_scope_values(value, field=info.field_name))


class MemoryCreate(ScopedRequest):
    content: str = Field(min_length=1)
    layer: MemoryLayer = "episodic"
    category: Optional[str] = None
    authority_attestation: Optional[str] = Field(
        default=None,
        description="Opaque signed authority candidate for a prohibition belief.",
    )
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    repo_id: Optional[str] = None
    tags: List[str] = Field(
        default_factory=list,
        description=(
            "Caller tags. Any provenance:* value is replaced by the server-owned HTTP tier."
        ),
    )
    metadata: Dict[str, Any] = Field(default_factory=dict)
    source_ids: List[str] = Field(default_factory=list)
    evidence_ids: List[str] = Field(default_factory=list)
    status: MemoryStatus = "active"
    source: Optional[str] = Field(
        default=None,
        deprecated=(
            "Accepted for compatibility but ignored on writes; the server assigns provenance."
        ),
    )
    quality_flags: List[str] = Field(default_factory=list)
    title: Optional[str] = None
    summary: Optional[str] = None
    observed_at: Optional[datetime] = None
    valid_from: Optional[datetime] = None
    valid_to: Optional[datetime] = None
    source_revision: Optional[str] = None
    source_hash: Optional[str] = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    entities: List[str] = Field(default_factory=list)
    files: List[str] = Field(default_factory=list)
    symbols: List[str] = Field(default_factory=list)
    keywords: List[str] = Field(default_factory=list)
    evidence: List[Dict[str, Any]] = Field(default_factory=list)
    lineage: List[str] = Field(default_factory=list)
    pinned: bool = False
    hold: bool = False

    @model_validator(mode="before")
    @classmethod
    def reject_caller_selected_initial_epistemic_status(cls, value):
        if isinstance(value, dict) and "epistemic_status" in value:
            raise ValueError(
                "initial epistemic status is assigned by the memory service"
            )
        return value

    @model_validator(mode="after")
    def validate_semantic_vocabulary(self):
        if self.layer == "semantic":
            self.category = normalize_belief_type(
                self.category or BeliefType.FACT.value
            )
        else:
            self.category = self.category or "note"
        return self


class MemoryResponse(BaseModel):
    id: str
    content: str
    layer: str
    category: str
    belief_type: Optional[BeliefType] = None
    epistemic_status: Optional[EpistemicStatus] = None
    importance: float = 0.5
    repo_id: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    status: MemoryStatus = "active"
    source: Optional[str] = None
    quality_flags: List[str] = Field(default_factory=list)
    evidence_ids: List[str] = Field(default_factory=list)
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    archived_at: Optional[datetime] = None
    created_at: datetime
    accessed_at: datetime
    similarity: Optional[float] = None
    relevance_score: Optional[float] = None
    title: Optional[str] = None
    summary: Optional[str] = None
    observed_at: Optional[datetime] = None
    valid_from: Optional[datetime] = None
    valid_to: Optional[datetime] = None
    source_revision: Optional[str] = None
    source_hash: Optional[str] = None
    confidence: float = 0.5
    entities: List[str] = Field(default_factory=list)
    files: List[str] = Field(default_factory=list)
    symbols: List[str] = Field(default_factory=list)
    keywords: List[str] = Field(default_factory=list)
    evidence: List[Dict[str, Any]] = Field(default_factory=list)
    lineage: List[str] = Field(default_factory=list)
    pinned: bool = False
    hold: bool = False
    environment: List[str] = Field(default_factory=list)
    task_type: List[str] = Field(default_factory=list)


class EvidenceCreate(BaseModel):
    content: str = Field(min_length=1)
    repo_id: str = Field(min_length=1)
    evidence_type: str = Field(default="observation", min_length=1)
    provenance: Optional[str] = Field(
        default=None,
        deprecated="Accepted for compatibility but ignored; HTTP Evidence is external.",
    )
    metadata: Dict[str, Any] = Field(default_factory=dict)


class EvidenceResponse(BaseModel):
    id: str
    content: str
    content_hash: str
    repo_id: str
    evidence_type: str
    provenance: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    record_type: Literal["evidence"] = "evidence"


class EvidenceAttachRequest(BaseModel):
    repo_id: str = Field(min_length=1)
    evidence_ids: List[str] = Field(min_length=1)


class RelatedMemoryResponse(MemoryResponse):
    relationship: str
    strength: float = 1.0
    relationship_evidence: Optional["RelationshipEvidence"] = None


class SessionCreateResponse(BaseModel):
    id: str
    status: Literal["started"] = "started"


class SessionCreateRequest(BaseModel):
    repo_id: str = Field(min_length=1)


class SessionResponse(BaseModel):
    id: str
    owner_id: Optional[str] = None
    team_id: Optional[str] = None
    repo_id: Optional[str] = None
    summary: Optional[str] = None
    memory_ids: List[str] = Field(default_factory=list)
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None


class SessionCompleteRequest(BaseModel):
    summary: str = ""
    memory_ids: List[str] = Field(default_factory=list)


class SessionCompleteResponse(BaseModel):
    id: str
    status: Literal["completed"] = "completed"
    intent_evaluations: Optional[List[Dict[str, Any]]] = None


class SearchQuery(ScopedRequest):
    query: str = Field(min_length=1)
    layers: Optional[List[MemoryLayer]] = None
    repo_id: Optional[str] = None
    status: MemoryStatus = "active"
    limit: int = Field(default=10, ge=1, le=MAX_QUERY_LIMIT)
    min_score: float = Field(default=DEFAULT_RECALL_MIN_SCORE, ge=0.0, le=1.0)
    as_of: Optional[datetime] = None


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


class GraphTraceRequest(ScopedRequest):
    query: str = Field(min_length=1)
    repo_id: Optional[str] = None
    depth: int = Field(default=2, ge=0, le=4)
    token_budget: int = Field(default=2000, ge=1, le=100000)
    limit: int = Field(default=5, ge=1, le=50)
    relationship_filter: Optional[str] = None
    as_of: Optional[datetime] = None


class GraphNeighborsRequest(ScopedRequest):
    memory_id: str = Field(min_length=1)
    repo_id: Optional[str] = None
    relationship_filter: Optional[str] = None
    depth: int = Field(default=1, ge=0, le=4)
    token_budget: int = Field(default=2000, ge=1, le=100000)
    limit: int = Field(default=25, ge=1, le=100)
    as_of: Optional[datetime] = None


class GraphPathRequest(ScopedRequest):
    source_id: str = Field(min_length=1)
    target_id: str = Field(min_length=1)
    repo_id: Optional[str] = None
    max_hops: int = Field(default=4, ge=1, le=6)
    token_budget: int = Field(default=2000, ge=1, le=100000)
    as_of: Optional[datetime] = None


class GraphWhyRelevantRequest(ScopedRequest):
    query: str = Field(min_length=1)
    memory_id: str = Field(min_length=1)
    repo_id: Optional[str] = None
    depth: int = Field(default=2, ge=0, le=4)
    token_budget: int = Field(default=2000, ge=1, le=100000)
    limit: int = Field(default=5, ge=1, le=50)
    as_of: Optional[datetime] = None


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
    status: Optional[IntentStatus] = Field(
        default=None,
        deprecated=(
            "Intent status is externally owned. REST status updates are rejected; "
            "use an outcome-history surface to retain an external decision."
        ),
    )
    context: Optional[Dict[str, Any]] = None


class IntentResponse(IntentCreate):
    id: str
    status: IntentStatus
    created_at: datetime
    updated_at: Optional[datetime] = None


class IntentEvaluationRequest(BaseModel):
    summary: str = Field(min_length=1, max_length=20000)
    memory_ids: List[str] = Field(default_factory=list)
    allow_auto_complete: bool = Field(
        default=True,
        deprecated=(
            "Accepted for one compatibility cycle but ineffective: "
            "evaluation is advisory and never changes intent status."
        ),
    )


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


class MemoryRevision(BaseModel):
    """Request to create an evidence-backed successor for a semantic belief."""

    content: str = Field(min_length=1)
    evidence_ids: List[str] = Field(min_length=1)
    authority_attestation: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    quality_flags: Optional[List[str]] = None
    reason: Optional[str] = None
    importance: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    tags: Optional[List[str]] = None


class MemoryMergePreviewRequest(BaseModel):
    memory_ids: List[str] = Field(min_length=2)
    target_id: Optional[str] = None
    target_content: Optional[str] = None


class MemoryMergeRequest(MemoryMergePreviewRequest):
    reviewed: bool = False


class MemoryPurgeRequest(BaseModel):
    memory_ids: List[str] = Field(min_length=1)
    confirmation: Optional[str] = None


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


class AskMemoryRequest(ScopedRequest):
    query: str = Field(min_length=1)
    repo_id: Optional[str] = None
    layers: Optional[List[MemoryLayer]] = None
    category: Optional[str] = None
    limit: int = Field(default=5, ge=1, le=20)
    require_citations: bool = True
    as_of: Optional[datetime] = None


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
    provider: Optional[str] = None
    model: Optional[str] = None


class ContextCompileRequest(ScopedRequest):
    query: str = Field(min_length=1, max_length=20000)
    repo_id: Optional[str] = None
    token_budget: int = Field(default=2000, ge=64, le=100000)
    as_of: Optional[datetime] = None
    files: List[str] = Field(default_factory=list)
    symbols: List[str] = Field(default_factory=list)
    previous_fingerprint: Optional[str] = None
    min_confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class TaskMemoryBriefRequest(ScopedRequest):
    task: str = Field(min_length=1, max_length=20000)
    repo_id: Optional[str] = None
    token_budget: int = Field(default=2000, ge=128, le=100000)
    as_of: Optional[datetime] = None
    files: List[str] = Field(default_factory=list)
    symbols: List[str] = Field(default_factory=list)
    intent_id: Optional[str] = None
    constraints: List[str] = Field(default_factory=list)
    previous_fingerprint: Optional[str] = None
    min_confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class ReflectionCreateRequest(BaseModel):
    repo_id: str
    title: str = Field(min_length=3, max_length=200)
    evidence_ids: List[str] = Field(min_length=2)
    reviewed: bool = False


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
    status: Literal["active", "archived"] = "active"
    archived_at: Optional[datetime] = None
    created_at: datetime


class ProjectScopeResponse(BaseModel):
    id: str
    name: str
    registered: bool = False
    status: Literal["active", "archived"] = "active"


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


class StorageDiagnosticsResponse(BaseModel):
    backend: str
    capabilities: Dict[str, bool]
    schema_status: Dict[str, Any]


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
