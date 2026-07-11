export type MemoryLayer = "episodic" | "semantic" | "intent"

export interface Memory {
  id: string
  content: string
  layer: MemoryLayer
  category: string
  status?: "active" | "pending" | "archived" | "superseded" | "merged" | "deleted"
  repoId?: string | null
  createdAt: string
  accessedAt?: string
  importance?: number
  accessCount?: number
  tags?: string[]
  metadata?: Record<string, unknown>
}

export interface SearchResult extends Memory {
  similarity?: number
}

export type RelationshipConfidence = "observed" | "inferred" | "ambiguous" | "manual"

export interface RelationshipEvidence {
  confidence?: RelationshipConfidence
  confidence_score?: number
  source?: string | null
  source_file?: string | null
  source_location?: string | null
  reason?: string | null
  created_by?: string | null
  created_at?: string | null
}

export interface GraphNode {
  id: string
  group?: string
  label: string
  full_label?: string
  radius?: number
  layer: MemoryLayer
}

export interface GraphLink {
  source: string
  target: string
  value?: number
  label?: string
  evidence?: RelationshipEvidence | null
}

export interface GraphData {
  nodes: GraphNode[]
  links: GraphLink[]
}

export type MemoryIntelligenceReportKind = "stored_fact" | "inferred_recommendation"

export interface MemoryIntelligenceReportItem {
  type: "memory" | "relationship" | "intent" | "question"
  id: string
  title: string
  reason: string
  facts: Record<string, unknown>
}

export interface MemoryIntelligenceReportSection {
  key: string
  title: string
  kind: MemoryIntelligenceReportKind
  thresholds: Record<string, unknown>
  items: MemoryIntelligenceReportItem[]
}

export interface MemoryIntelligenceReport {
  schemaVersion: string
  repoId?: string | null
  asOf?: string | null
  thresholds: Record<string, unknown>
  summary: {
    totalMemories: number
    totalRelationships: number
    activeIntents: number
    nonEmptySections: number
  }
  sections: Record<string, MemoryIntelligenceReportSection>
}

export interface DuplicateCandidate {
  ids: string[]
  contents: string[]
  repoId?: string | null
  layer: string
  category?: string | null
  similarity: number
  reason: string
}

export interface QualityDuplicateResponse {
  candidates: DuplicateCandidate[]
}

export interface Intent {
  id: string
  description: string
  priority: "high" | "medium" | "low"
  priorityValue?: number
  status: "active" | "completed" | "closed"
  createdAt: string
  updatedAt?: string
  context?: Record<string, any>
}

export interface DecayPreviewItem {
  memoryId: string
  snippet: string
  layer: string
  category?: string | null
  repoId?: string | null
  currentImportance: number
  projectedImportance: number
  decayAmount: number
  ageDays: number
  accessCount: number
  lastAccessedAt?: string | null
  createdAt: string
  risk: "stable" | "weakening" | "likely_to_decay" | "at_floor"
  reason: string
}

export interface DecayPreviewResponse {
  halflifeDays: number
  minImportance: number
  decayEnabled: boolean
  candidates: DecayPreviewItem[]
}

export interface ProjectScope {
  id: string
  name: string
  registered: boolean
  status?: "active" | "archived"
}

export interface Project {
  id: string
  name: string
  url?: string | null
  description?: string | null
  techStack: string[]
  status: "active" | "archived"
  archivedAt?: string | null
  createdAt: string
}

export interface MemoryMergePreview {
  memoryIds: string[]
  targetId: string
  repoId?: string | null
  layer: string
  targetContent: string
  exactDuplicate: boolean
  validationErrors: string[]
  warnings: string[]
  relationshipRewrites: number
  sourceTokens: number
  resultTokens: number
  estimatedTokensSaved: number
  mergedTags: string[]
  operationId?: string
  status?: string
}

export interface Stats {
  totalMemories: number
  activeIntents: number
  knowledgeNodes: number
  connections: number
}

export type ProviderConnectionStatus =
  | "connected"
  | "failed"
  | "disabled"
  | "fallback"
  | "not_configured"
  | "not_checked"

export interface ProviderDiagnostic {
  provider: string
  status: ProviderConnectionStatus
  model?: string
  dimension?: number
  message?: string
  lastChecked?: string
}

export interface EmbeddingIndexStatus {
  storageBackend: string
  provider: string
  effectiveProvider?: string | null
  model?: string | null
  dimension?: number | null
  status: "available" | "disabled" | "not_configured" | "unknown"
  message: string
  scope: Record<string, string>
  matchedMemories: number
  indexedMemories?: number | null
  activeCollections: string[]
  legacyCollections: string[]
  needsReindex: boolean
}

export interface EmbeddingReindexResult {
  dryRun: boolean
  status: "ready" | "completed" | "partial_failure" | "disabled" | "not_configured" | "unsupported"
  message: string
  scope: Record<string, string>
  matchedMemories: number
  reindexedMemories: number
  failedMemories: number
  dimension?: number | null
  activeCollections: string[]
  legacyCollections: string[]
  errors: Array<Record<string, string>>
}

export interface RuntimeStatus {
  status: "online" | "ready" | "not_ready" | "offline"
  version?: string
  storageBackend?: string
  storageMode?: string
  vectorDb?: string
  embeddingProvider?: string
  embeddingEffectiveProvider?: string | null
  embeddingModel?: string
  embeddingDriverStatus?: "connected" | "failed" | "disabled" | "not_configured" | "fallback"
  embeddingDriverConnected?: boolean
  embeddingStatusMessage?: string
  embeddingConnectionError?: string
  authEnabled?: boolean
  repoId?: string | null
  storageReady?: boolean
  dashboardStaticAvailable?: boolean
}

export interface StorageCapabilities {
  graph: boolean
  vectorSearch: boolean
  repositories: boolean
  teams: boolean
  sessions: boolean
  auditLog: boolean
  reindex: boolean
}

export interface StorageDiagnostics {
  backend: string
  capabilities: StorageCapabilities
  schema: {
    currentVersion?: number
    storedVersion?: number
    status?: string
  }
}

export interface SystemStatus {
  apiServer: "online" | "offline"
  vectorDatabase: "ready" | "syncing" | "offline"
  embeddings: "active" | "inactive" | "offline"
  runtime?: RuntimeStatus
}

export interface ModelRoutingStatus {
  provider: string
  model?: string | null
  configured: boolean
  supportsClientSampling: boolean
  timeoutSeconds: number
  maxOutputTokens: number
  tasks: string[]
}

export interface AuthUser {
  id: string
  username: string
  email?: string | null
  displayName?: string | null
  role: "admin" | "user"
  teamId?: string | null
  enabled: boolean
  lastLoginAt?: string | null
}

export interface AuthSession {
  user: AuthUser
  authType: string
  scopes: string[]
  repoIds: string[]
  csrfToken?: string | null
}

export interface PersonalAccessToken {
  id: string
  name: string
  tokenPrefix: string
  scopes: string[]
  repoIds: string[]
  createdAt: string
  expiresAt?: string | null
  lastUsedAt?: string | null
  revokedAt?: string | null
  token?: string
}

export type TaskBriefSectionName = "warnings" | "decisions" | "knowledge" | "history"

export interface TaskBriefProfile {
  action: string
  keywords: string[]
  files: string[]
  symbols: string[]
}

export interface TaskBriefIntent {
  id: string
  description: string
  priority: number
  status: string
  acceptanceCriteria: string[]
}

export interface TaskBriefItem {
  id: string
  content: string
  layer?: string | null
  category?: string | null
  citation: string
  confidence?: number | null
  files: string[]
  symbols: string[]
  sourceRevision?: string | null
  retrievalChannels: string[]
  retrievalFactors: {
    directScore?: number
    directRank?: number | null
    entityRank?: number | null
    graphRank?: number | null
    graphScore?: number
    rrfScore?: number
    seedSpecificity?: number | null
  }
}

export interface TaskBriefCitation {
  citation: string
  memoryId: string
  layer?: string | null
  category?: string | null
  repoId?: string | null
  confidence?: number | null
  sourceRevision?: string | null
  sourceHash?: string | null
  observedAt?: string | null
  validFrom?: string | null
  validTo?: string | null
  files: string[]
  symbols: string[]
}

export interface TaskBriefContradiction {
  relationshipId?: string | null
  relationship: string
  citation: string
  memoryId: string
  otherMemoryId: string
  otherStatus: string
  otherSnippet: string
  evidence: Record<string, unknown>
}

export interface TaskMemoryBrief {
  schemaVersion: string
  task: string
  repoId?: string | null
  asOf?: string | null
  taskProfile: TaskBriefProfile
  intent?: TaskBriefIntent | null
  constraints: string[]
  unknowns: string[]
  contradictions: TaskBriefContradiction[]
  sections: Record<TaskBriefSectionName, TaskBriefItem[]>
  citations: TaskBriefCitation[]
  tokenBudget: number
  tokenCount: number
  fingerprint: string
  unchanged: boolean
  abstained: boolean
  abstentionReason?: string | null
  truncated: boolean
  context: string
  retrieval: {
    strategy: string
    candidateCount: number
    selectedCount: number
    channelCounts: Record<string, number>
  }
  metrics: {
    candidateCount: number
    selectedCount: number
    omittedCount: number
    sectionCounts: Record<TaskBriefSectionName, number>
  }
}
