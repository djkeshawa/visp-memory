
import {
    DecayPreviewResponse,
    EmbeddingIndexStatus,
    EmbeddingReindexResult,
    Memory,
    Intent,
    ProviderConnectionStatus,
    ProviderDiagnostic,
    RuntimeStatus,
    StorageDiagnostics,
    SearchResult,
    Stats,
    ProjectScope,
    GraphData,
    GraphLink,
    GraphNode,
    RelationshipEvidence,
    MemoryIntelligenceReport,
    MemoryIntelligenceReportItem,
    MemoryIntelligenceReportSection,
    QualityDuplicateResponse,
} from "./types"

const API_BASE_URL = process.env.NEXT_PUBLIC_LLM_MEMORY_API_URL || ""

export class ApiError extends Error {
    status: number
    detail?: string
    requestId?: string

    constructor(message: string, status: number, detail?: string, requestId?: string) {
        super(message)
        this.name = "ApiError"
        this.status = status
        this.detail = detail
        this.requestId = requestId
    }
}

export function isApiError(error: unknown): error is ApiError {
    return error instanceof ApiError
}

export function hasAuthCredentials(): boolean {
    const { apiKey, jwtToken } = getAuthCredentials()

    return Boolean(apiKey || jwtToken)
}

export function describeApiError(error: unknown): string {
    if (!isApiError(error)) {
        return "The server could not be reached. Confirm the LLM Memory server is running."
    }

    if (error.status === 0) {
        return "The server could not be reached. Confirm the LLM Memory server is running."
    }

    if (error.status === 401) {
        const requestId = error.requestId ? ` Request ID: ${error.requestId}.` : ""
        if (!hasAuthCredentials()) {
            return `Authentication is required. Add an API key or JWT token before loading protected data.${requestId}`
        }

        return `The configured credentials were rejected${error.detail ? `: ${error.detail}` : "."}${requestId}`
    }

    const requestId = error.requestId ? ` Request ID: ${error.requestId}.` : ""
    return `The server returned HTTP ${error.status}${error.detail ? `: ${error.detail}` : "."}${requestId}`
}

const API_KEY_STORAGE_KEY = "llm-memory-api-key"
const JWT_STORAGE_KEY = "llm-memory-jwt-token"
export const AUTH_CHANGED_EVENT = "llm-memory-auth-changed"

export function getAuthCredentials(): { apiKey: string; jwtToken: string } {
    if (typeof window === "undefined") return { apiKey: "", jwtToken: "" }
    return {
        apiKey: window.sessionStorage.getItem(API_KEY_STORAGE_KEY) || "",
        jwtToken: window.sessionStorage.getItem(JWT_STORAGE_KEY) || "",
    }
}

export function setAuthCredentials(apiKey: string, jwtToken: string): void {
    if (typeof window === "undefined") return
    const trimmedApiKey = apiKey.trim()
    const trimmedJwtToken = jwtToken.trim()
    if (trimmedApiKey) window.sessionStorage.setItem(API_KEY_STORAGE_KEY, trimmedApiKey)
    else window.sessionStorage.removeItem(API_KEY_STORAGE_KEY)
    if (trimmedJwtToken) window.sessionStorage.setItem(JWT_STORAGE_KEY, trimmedJwtToken)
    else window.sessionStorage.removeItem(JWT_STORAGE_KEY)
    window.dispatchEvent(new Event(AUTH_CHANGED_EVENT))
}

export function clearAuthCredentials(): void {
    setAuthCredentials("", "")
}

function authHeaders(): HeadersInit {
    const headers: Record<string, string> = {}
    const { apiKey, jwtToken } = getAuthCredentials()

    if (jwtToken) headers.Authorization = `Bearer ${jwtToken}`
    else if (apiKey) headers["X-API-KEY"] = apiKey

    return headers
}

function jsonHeaders(): HeadersInit {
    return {
        "Content-Type": "application/json",
        ...authHeaders(),
    }
}

async function request(path: string, init?: RequestInit): Promise<Response> {
    try {
        const res = await fetch(`${API_BASE_URL}${path}`, init)
        if (!res.ok) {
            throw await apiErrorFromResponse(res)
        }
        return res
    } catch (error) {
        if (isApiError(error)) throw error
        throw new ApiError("Server unreachable", 0, "Could not connect to the LLM Memory server")
    }
}

function withQuery(path: string, params: Record<string, string | number | null | undefined>): string {
    const searchParams = new URLSearchParams()
    for (const [key, value] of Object.entries(params)) {
        if (value !== undefined && value !== null && value !== "") {
            searchParams.set(key, String(value))
        }
    }

    const query = searchParams.toString()
    return query ? `${path}?${query}` : path
}

async function apiErrorFromResponse(res: Response): Promise<ApiError> {
    const detail = await readErrorDetail(res)
    const message = detail || `Request failed with HTTP ${res.status}`
    return new ApiError(message, res.status, detail, res.headers.get("X-Request-ID") || undefined)
}

async function readErrorDetail(res: Response): Promise<string | undefined> {
    const contentType = res.headers.get("content-type") || ""

    try {
        if (contentType.includes("application/json")) {
            const body = await res.json()
            const detail = body?.detail || body?.message || body?.error
            if (Array.isArray(detail)) return detail.map((item) => item?.msg || String(item)).join("; ")
            if (detail) return String(detail)
        } else {
            const text = await res.text()
            if (text.trim()) return text.trim()
        }
    } catch {
        return undefined
    }

    return undefined
}

export async function getStats(repoId?: string | null): Promise<Stats> {
    const res = await request(withQuery("/", { repo_id: repoId }), { headers: authHeaders() })
    const data = await res.json()
    const stats = data.stats || data

    return {
        totalMemories: stats.total_memories || 0,
        activeIntents: stats.active_intents || 0,
        knowledgeNodes: stats.memories_by_layer?.semantic || 0,
        connections: stats.total_relationships || 0,
    }
}

export async function getProjectScopes(): Promise<ProjectScope[]> {
    const res = await request("/repos/scopes", { headers: authHeaders() })
    const data = await res.json()

    return data.map((item: any) => ({
        id: item.id,
        name: item.name || item.id,
        registered: Boolean(item.registered),
    }))
}

export async function getRuntimeStatus(): Promise<RuntimeStatus> {
    const res = await request("/", { headers: authHeaders() })
    const data = await res.json()

    return {
        status: data.status || "online",
        version: data.version,
        storageBackend: data.storage_backend,
        storageMode: data.storage_mode,
        vectorDb: data.vector_db,
        embeddingProvider: data.embedding_provider,
        embeddingEffectiveProvider: data.embedding_effective_provider,
        embeddingModel: data.embedding_model,
        embeddingDriverStatus: data.embedding_driver_status,
        embeddingDriverConnected: data.embedding_driver_connected,
        embeddingStatusMessage: data.embedding_status_message,
        embeddingConnectionError: data.embedding_connection_error,
        authEnabled: data.auth_enabled,
        repoId: data.repo_id,
        storageReady: data.storage_ready,
        dashboardStaticAvailable: data.dashboard_static_available,
    }
}

export async function getStorageDiagnostics(): Promise<StorageDiagnostics> {
    const res = await request("/diagnostics/storage", { headers: authHeaders() })
    const data = await res.json()
    const capabilities = data.capabilities || {}
    const schema = data.schema_status || data.schema || {}
    return {
        backend: readString(data.backend) || "unknown",
        capabilities: {
            graph: Boolean(capabilities.graph),
            vectorSearch: Boolean(capabilities.vector_search),
            repositories: Boolean(capabilities.repositories),
            teams: Boolean(capabilities.teams),
            sessions: Boolean(capabilities.sessions),
            auditLog: Boolean(capabilities.audit_log),
            reindex: Boolean(capabilities.reindex),
        },
        schema: {
            currentVersion: readNumber(schema.current_version),
            storedVersion: readNumber(schema.stored_version),
            status: readString(schema.status),
        },
    }
}

export async function getRecentMemories(limit: number = 8, repoId?: string | null): Promise<Memory[]> {
    const res = await request(withQuery("/memories", { limit, repo_id: repoId }), { headers: authHeaders() })
    const data = await res.json()

    return data.map((item: any) => ({
        id: item.id,
        content: item.content,
        layer: item.layer,
        category: item.category,
        status: item.status,
        createdAt: item.created_at,
        accessedAt: item.accessed_at,
        importance: item.importance,
        accessCount: item.access_count,
        tags: item.tags,
    }))
}

export async function getIntents(repoId?: string | null, status: string = "active"): Promise<Intent[]> {
    const res = await request(withQuery("/intents", { repo_id: repoId, status }), { headers: authHeaders() })
    const data = await res.json()

    return data.map((item: any) => ({
        id: item.id,
        description: item.description,
        priority: mapPriority(item.priority),
        priorityValue: readNumber(item.priority) ?? 1,
        status: item.status,
        createdAt: item.created_at,
        updatedAt: item.updated_at,
    }))
}

export async function searchMemories(query: string, limit: number = 10, repoId?: string | null): Promise<SearchResult[]> {
    const res = await request("/recall", {
        method: "POST",
        headers: jsonHeaders(),
        body: JSON.stringify({ query, limit, repo_id: repoId || undefined }),
    })

    const data = await res.json()

    return data.map((item: any) => ({
        id: item.id,
        content: item.content,
        layer: item.layer,
        category: item.category,
        status: item.status,
        createdAt: item.created_at,
        similarity: item.similarity,
        importance: item.importance,
    }))
}

export async function getGraphData(repoId?: string | null): Promise<GraphData> {
    const res = await request(withQuery("/graph", { repo_id: repoId }), { headers: authHeaders() })
    const data = await res.json()

    return {
        nodes: Array.isArray(data.nodes) ? data.nodes.map(toGraphNode) : [],
        links: Array.isArray(data.links) ? data.links.map(toGraphLink) : [],
    }
}

export async function getMemoryIntelligenceReport(
    repoId?: string | null,
    limit: number = 10,
): Promise<MemoryIntelligenceReport> {
    const res = await request(
        withQuery("/reports/memory-intelligence", { repo_id: repoId, limit }),
        { headers: authHeaders() },
    )
    const data = await res.json()

    return parseMemoryIntelligenceReport(data)
}

export async function getDuplicateCandidates(options: {
    repoId?: string | null
    limit?: number
    layer?: string | null
    category?: string | null
} = {}): Promise<QualityDuplicateResponse> {
    const res = await request(
        withQuery("/quality/duplicates", {
            repo_id: options.repoId,
            limit: options.limit,
            layer: options.layer,
            category: options.category,
        }),
        { headers: authHeaders() },
    )
    const data = await res.json()

    return {
        candidates: Array.isArray(data.candidates)
            ? data.candidates.map((item: Record<string, unknown>) => ({
                ids: readStringArray(item.ids),
                contents: readStringArray(item.contents),
                repoId: readString(item.repo_id) ?? null,
                layer: readString(item.layer) || "unknown",
                category: readString(item.category) ?? null,
                similarity: readNumber(item.similarity) ?? 0,
                reason: readString(item.reason) || "Duplicate candidate.",
            }))
            : [],
    }
}

export async function createIntent(description: string, priority: number, repoId?: string | null): Promise<Intent> {
    const res = await request("/intents", {
        method: "POST",
        headers: jsonHeaders(),
        body: JSON.stringify({ description, priority: toApiPriority(priority), repo_id: repoId || undefined, context: {} })
    })

    const data = await res.json()

    return {
        id: data.id,
        description: data.description,
        priority: mapPriority(data.priority),
        priorityValue: readNumber(data.priority) ?? toApiPriority(priority),
        status: data.status,
        createdAt: data.created_at,
        updatedAt: data.updated_at,
    }
}

export async function updateIntent(
    intentId: string,
    updates: { description?: string; priority?: number; status?: "active" | "completed" | "closed" },
): Promise<Intent> {
    const payload: Record<string, unknown> = {}
    if (updates.description !== undefined) payload.description = updates.description
    if (updates.priority !== undefined) payload.priority = toApiPriority(updates.priority)
    if (updates.status !== undefined) payload.status = updates.status

    const res = await request(`/intents/${encodeURIComponent(intentId)}`, {
        method: "PATCH",
        headers: jsonHeaders(),
        body: JSON.stringify(payload),
    })
    const data = await res.json()

    return {
        id: data.id,
        description: data.description,
        priority: mapPriority(data.priority),
        priorityValue: readNumber(data.priority) ?? 1,
        status: data.status,
        createdAt: data.created_at,
        updatedAt: data.updated_at,
    }
}

export async function closeIntent(intentId: string): Promise<void> {
    await request(`/intents/${encodeURIComponent(intentId)}/close`, {
        method: "POST",
        headers: authHeaders(),
    })
}

export async function completeIntent(intentId: string): Promise<void> {
    await request(`/intents/${encodeURIComponent(intentId)}/complete`, {
        method: "POST",
        headers: authHeaders(),
    })
}

export async function getDecayPreview(options: {
    repoId?: string | null
    limit?: number
    halflifeDays?: number
    minImportance?: number
}): Promise<DecayPreviewResponse> {
    const res = await request(
        withQuery("/quality/decay-preview", {
            repo_id: options.repoId,
            limit: options.limit,
            halflife_days: options.halflifeDays,
            min_importance: options.minImportance,
        }),
        { headers: authHeaders() },
    )
    const data = await res.json()

    return {
        halflifeDays: readNumber(data.halflife_days) ?? 30,
        minImportance: readNumber(data.min_importance) ?? 0.1,
        decayEnabled: Boolean(data.decay_enabled),
        candidates: Array.isArray(data.candidates)
            ? data.candidates.map((item: any) => ({
                memoryId: item.memory_id,
                snippet: item.snippet,
                layer: item.layer,
                category: item.category,
                repoId: item.repo_id,
                currentImportance: readNumber(item.current_importance) ?? 0,
                projectedImportance: readNumber(item.projected_importance) ?? 0,
                decayAmount: readNumber(item.decay_amount) ?? 0,
                ageDays: readNumber(item.age_days) ?? 0,
                accessCount: readNumber(item.access_count) ?? 0,
                lastAccessedAt: item.last_accessed_at,
                createdAt: item.created_at,
                risk: parseDecayRisk(item.risk),
                reason: item.reason || "",
            }))
            : [],
    }
}

export async function createMemory(content: string, category: string, tags: string[], repoId?: string | null): Promise<any> {
    const res = await request("/memories", {
        method: "POST",
        headers: jsonHeaders(),
        body: JSON.stringify({
            content,
            category,
            tags,
            layer: "episodic",
            importance: 0.5,
            repo_id: repoId || undefined
        })
    })

    return res.json()
}

export async function getProviderDiagnostics(): Promise<ProviderDiagnostic[]> {
    const res = await request("/diagnostics/providers", { headers: authHeaders() })
    const data = await res.json()
    const diagnostics = extractProviderDiagnosticsPayload(data)

    return diagnostics
        .map((item, index) => parseProviderDiagnostic(item, index))
        .filter((provider) => Boolean(provider.provider))
        .sort((left, right) => left.provider.localeCompare(right.provider))
}

export async function testProvider(provider: string): Promise<ProviderDiagnostic> {
    const res = await request(`/diagnostics/providers/${encodeURIComponent(provider)}/test`, {
        method: "POST",
        headers: authHeaders(),
    })
    const data = await res.json()

    return parseProviderDiagnostic(data, 0, provider)
}

export async function getEmbeddingIndexStatus(repoId?: string | null): Promise<EmbeddingIndexStatus> {
    const res = await request(withQuery("/diagnostics/embedding-index", { repo_id: repoId }), {
        headers: authHeaders(),
    })
    const data = await res.json()

    return parseEmbeddingIndexStatus(data)
}

export async function reindexEmbeddingIndex(options: {
    repoId?: string | null
    layer?: string | null
    category?: string | null
    dryRun?: boolean
}): Promise<EmbeddingReindexResult> {
    const res = await request("/diagnostics/embedding-index/reindex", {
        method: "POST",
        headers: jsonHeaders(),
        body: JSON.stringify({
            repo_id: options.repoId || undefined,
            layer: options.layer || undefined,
            category: options.category || undefined,
            dry_run: options.dryRun ?? true,
        }),
    })
    const data = await res.json()

    return parseEmbeddingReindexResult(data)
}

function parseEmbeddingIndexStatus(data: Record<string, unknown>): EmbeddingIndexStatus {
    return {
        storageBackend: readString(data.storage_backend) || "unknown",
        provider: readString(data.provider) || "unknown",
        effectiveProvider: readString(data.effective_provider) || null,
        model: readString(data.model) || null,
        dimension: readNumber(data.dimension) ?? null,
        status: parseIndexStatus(data.status),
        message: readString(data.message) || "Embedding index status is unavailable.",
        scope: readStringRecord(data.scope),
        matchedMemories: readNumber(data.matched_memories) ?? 0,
        indexedMemories: readNumber(data.indexed_memories) ?? null,
        activeCollections: readStringArray(data.active_collections),
        legacyCollections: readStringArray(data.legacy_collections),
        needsReindex: Boolean(data.needs_reindex),
    }
}

function parseEmbeddingReindexResult(data: Record<string, unknown>): EmbeddingReindexResult {
    return {
        dryRun: Boolean(data.dry_run),
        status: parseReindexStatus(data.status),
        message: readString(data.message) || "Embedding reindex status is unavailable.",
        scope: readStringRecord(data.scope),
        matchedMemories: readNumber(data.matched_memories) ?? 0,
        reindexedMemories: readNumber(data.reindexed_memories) ?? 0,
        failedMemories: readNumber(data.failed_memories) ?? 0,
        dimension: readNumber(data.dimension) ?? null,
        activeCollections: readStringArray(data.active_collections),
        legacyCollections: readStringArray(data.legacy_collections),
        errors: Array.isArray(data.errors)
            ? data.errors.filter((item): item is Record<string, string> => Boolean(item) && typeof item === "object") as Array<Record<string, string>>
            : [],
    }
}

function parseProviderDiagnostic(
    item: unknown,
    index: number,
    fallbackProvider = "unknown",
): ProviderDiagnostic {
    if (!item || typeof item !== "object") {
        return {
            provider: fallbackProvider || `provider-${index + 1}`,
            status: "not_configured",
        }
    }

    const record = item as Record<string, unknown>
    const nested = maybeNestedDiagnostic(record)
    const source = nested && typeof nested === "object" ? (nested as Record<string, unknown>) : record

    return {
        provider:
            readProviderField(source) ||
            readProviderField(record) ||
            fallbackProvider ||
            `provider-${index + 1}`,
        status: resolveStatus(source),
        model: readString(source.model) ?? readString(record.model) ?? readString(source.embedding_model) ?? readString(record.embedding_model),
        dimension:
            readNumber(source.dimension) ??
            readNumber(record.dimension) ??
            readNumber(source.embedding_dimension) ??
            readNumber(record.embedding_dimension),
        message:
            readString(source.message) ??
            readString(record.message) ??
            readString(source.embedding_status_message) ??
            readString(record.embedding_status_message),
        lastChecked:
            readString(source.last_checked) ??
            readString(record.last_checked) ??
            readString(source.lastChecked) ??
            readString(record.lastChecked) ??
            readString(source.last_checked_at) ??
            readString(record.last_checked_at) ??
            readString(source.last_check) ??
            readString(record.last_check),
    }
}

function parseIndexStatus(value: unknown): EmbeddingIndexStatus["status"] {
    const status = readString(value)
    if (status === "available" || status === "disabled" || status === "not_configured" || status === "unknown") {
        return status
    }
    return "unknown"
}

function parseReindexStatus(value: unknown): EmbeddingReindexResult["status"] {
    const status = readString(value)
    if (
        status === "ready" ||
        status === "completed" ||
        status === "partial_failure" ||
        status === "disabled" ||
        status === "not_configured" ||
        status === "unsupported"
    ) {
        return status
    }
    return "unsupported"
}

function parseDecayRisk(value: unknown): DecayPreviewResponse["candidates"][number]["risk"] {
    const risk = readString(value)
    if (risk === "stable" || risk === "weakening" || risk === "likely_to_decay" || risk === "at_floor") {
        return risk
    }
    return "stable"
}

function resolveStatus(record: Record<string, unknown>): ProviderConnectionStatus {
    const status = readString(
        record.status || record.state || record.connection_status || record.driver_status,
    )?.toLowerCase()

    if (status === "connected" || status === "failed" || status === "disabled" || status === "fallback" || status === "not_configured" || status === "not_checked") {
        return status
    }
    if (status === "not configured") return "not_configured"
    if (status === "online") return "connected"
    if (status === "active") return "connected"
    if (status === "ready") return "connected"
    if (status === "offline") return "failed"

    if (record.embedding_driver_connected === true) return "connected"
    if (record.embedding_driver_connected === false) return "failed"
    if (record.connected === true) return "connected"
    if (record.connected === false) return "failed"

    return "not_configured"
}

function maybeNestedDiagnostic(record: Record<string, unknown>): Record<string, unknown> | undefined {
    if (typeof record.provider === "object" && record.provider !== null && !Array.isArray(record.provider)) {
        const nested = record.provider
        return nested as Record<string, unknown>
    }

    if (typeof record.data === "object" && record.data !== null && !Array.isArray(record.data)) {
        return record.data as Record<string, unknown>
    }

    if (typeof record.result === "object" && record.result !== null && !Array.isArray(record.result)) {
        return record.result as Record<string, unknown>
    }

    return undefined
}

function extractProviderDiagnosticsPayload(payload: unknown): unknown[] {
    if (Array.isArray(payload)) {
        return payload
    }

    if (!payload || typeof payload !== "object") return []

    const record = payload as Record<string, unknown>

    for (const key of ["providers", "diagnostics", "data", "result"]) {
        const candidate = record[key]
        if (Array.isArray(candidate)) return candidate
    }

    return []
}

function readProviderField(record: Record<string, unknown>): string | undefined {
    const provider = readString(record.provider) || readString(record.name)
    return provider ? provider.trim() : undefined
}

function readString(value: unknown): string | undefined {
    if (typeof value === "string") {
        const trimmed = value.trim()
        return trimmed.length > 0 ? trimmed : undefined
    }

    return undefined
}

function toGraphNode(record: Record<string, unknown>): GraphNode {
    return {
        id: readString(record.id) ?? "",
        group: readString(record.group),
        label: readString(record.label) ?? "",
        full_label: readString(record.full_label),
        radius: readNumber(record.radius),
        layer: (readString(record.layer) as GraphNode["layer"]) ?? "episodic",
    }
}

function toGraphLink(record: Record<string, unknown>): GraphLink {
    return {
        source: readString(record.source) ?? "",
        target: readString(record.target) ?? "",
        value: readNumber(record.value),
        label: readString(record.label),
        evidence: readRelationshipEvidence(record.evidence),
    }
}

function readRelationshipEvidence(value: unknown): RelationshipEvidence | null {
    if (!value || typeof value !== "object") return null
    const record = value as Record<string, unknown>
    return {
        confidence: readString(record.confidence) as RelationshipEvidence["confidence"],
        confidence_score: readNumber(record.confidence_score),
        source: readString(record.source) ?? null,
        source_file: readString(record.source_file) ?? null,
        source_location: readString(record.source_location) ?? null,
        reason: readString(record.reason) ?? null,
        created_by: readString(record.created_by) ?? null,
        created_at: readString(record.created_at) ?? null,
    }
}

function parseMemoryIntelligenceReport(data: Record<string, unknown>): MemoryIntelligenceReport {
    const summary = readUnknownRecord(data.summary)
    const rawSections = readUnknownRecord(data.sections)
    const sections: Record<string, MemoryIntelligenceReportSection> = {}

    for (const [key, value] of Object.entries(rawSections)) {
        sections[key] = parseReportSection(key, value)
    }

    return {
        schemaVersion: readString(data.schema_version) || "unknown",
        repoId: readString(data.repo_id) ?? null,
        asOf: readString(data.as_of) ?? null,
        thresholds: readUnknownRecord(data.thresholds),
        summary: {
            totalMemories: readNumber(summary.total_memories) ?? 0,
            totalRelationships: readNumber(summary.total_relationships) ?? 0,
            activeIntents: readNumber(summary.active_intents) ?? 0,
            nonEmptySections: readNumber(summary.non_empty_sections) ?? 0,
        },
        sections,
    }
}

function parseReportSection(key: string, value: unknown): MemoryIntelligenceReportSection {
    const record = readUnknownRecord(value)
    return {
        key: readString(record.key) || key,
        title: readString(record.title) || key,
        kind: parseReportKind(record.kind),
        thresholds: readUnknownRecord(record.thresholds),
        items: readReportItems(record.items),
    }
}

function parseReportKind(value: unknown): MemoryIntelligenceReportSection["kind"] {
    const kind = readString(value)
    if (kind === "stored_fact" || kind === "inferred_recommendation") return kind
    return "inferred_recommendation"
}

function readReportItems(value: unknown): MemoryIntelligenceReportItem[] {
    if (!Array.isArray(value)) return []

    return value.map((item) => {
        const record = readUnknownRecord(item)
        return {
            type: parseReportItemType(record.type),
            id: readString(record.id) || readString(record.title) || "finding",
            title: readString(record.title) || readString(record.id) || "Finding",
            reason: readString(record.reason) || "",
            facts: readUnknownRecord(record.facts),
        }
    })
}

function parseReportItemType(value: unknown): MemoryIntelligenceReportItem["type"] {
    const type = readString(value)
    if (type === "memory" || type === "relationship" || type === "intent" || type === "question") {
        return type
    }
    return "memory"
}

function readNumber(value: unknown): number | undefined {
    if (typeof value === "number" && Number.isFinite(value)) {
        return value
    }
    if (typeof value === "string") {
        const parsed = Number(value)
        return Number.isFinite(parsed) ? parsed : undefined
    }

    return undefined
}

function readStringArray(value: unknown): string[] {
    if (!Array.isArray(value)) return []
    return value.filter((item): item is string => typeof item === "string" && item.length > 0)
}

function readUnknownRecord(value: unknown): Record<string, unknown> {
    if (!value || typeof value !== "object" || Array.isArray(value)) return {}
    return value as Record<string, unknown>
}

function readStringRecord(value: unknown): Record<string, string> {
    if (!value || typeof value !== "object" || Array.isArray(value)) return {}

    const output: Record<string, string> = {}
    for (const [key, item] of Object.entries(value)) {
        if (typeof item === "string") {
            output[key] = item
        }
    }
    return output
}

function mapPriority(priority: number): "high" | "medium" | "low" {
    if (priority >= 3) return "high"
    if (priority >= 2) return "medium"
    return "low"
}

function toApiPriority(priority: number): number {
    if (priority >= 8) return 3
    if (priority >= 4) return 2
    return 1
}
