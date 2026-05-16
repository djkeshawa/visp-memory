
import { Memory, Intent, Stats, SearchResult } from "./types"

const API_BASE_URL = process.env.NEXT_PUBLIC_LLM_MEMORY_API_URL || ""

export class ApiError extends Error {
    status: number
    detail?: string

    constructor(message: string, status: number, detail?: string) {
        super(message)
        this.name = "ApiError"
        this.status = status
        this.detail = detail
    }
}

export function isApiError(error: unknown): error is ApiError {
    return error instanceof ApiError
}

export function hasAuthCredentials(): boolean {
    const apiKey =
        process.env.NEXT_PUBLIC_LLM_MEMORY_API_KEY ||
        (typeof window !== "undefined" ? window.localStorage.getItem("llm-memory-api-key") : null)
    const jwtToken =
        process.env.NEXT_PUBLIC_LLM_MEMORY_JWT_TOKEN ||
        (typeof window !== "undefined" ? window.localStorage.getItem("llm-memory-jwt-token") : null)

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
        if (!hasAuthCredentials()) {
            return "Authentication is required. Add an API key or JWT token before loading protected data."
        }

        return `The configured credentials were rejected${error.detail ? `: ${error.detail}` : "."}`
    }

    return `The server returned HTTP ${error.status}${error.detail ? `: ${error.detail}` : "."}`
}

function authHeaders(): HeadersInit {
    const headers: Record<string, string> = {}

    const apiKey =
        process.env.NEXT_PUBLIC_LLM_MEMORY_API_KEY ||
        (typeof window !== "undefined" ? window.localStorage.getItem("llm-memory-api-key") : null)
    const jwtToken =
        process.env.NEXT_PUBLIC_LLM_MEMORY_JWT_TOKEN ||
        (typeof window !== "undefined" ? window.localStorage.getItem("llm-memory-jwt-token") : null)

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

async function apiErrorFromResponse(res: Response): Promise<ApiError> {
    const detail = await readErrorDetail(res)
    const message = detail || `Request failed with HTTP ${res.status}`
    return new ApiError(message, res.status, detail)
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

export async function getStats(): Promise<Stats> {
    const res = await request("/", { headers: authHeaders() })
    const data = await res.json()
    const stats = data.stats || data

    return {
        totalMemories: stats.total_memories || 0,
        activeIntents: stats.active_intents || 0,
        knowledgeNodes: stats.memories_by_layer?.semantic || 0,
        connections: stats.total_relationships || 0,
    }
}

export async function getRecentMemories(limit: number = 8): Promise<Memory[]> {
    const res = await request(`/memories?limit=${limit}`, { headers: authHeaders() })
    const data = await res.json()

    return data.map((item: any) => ({
        id: item.id,
        content: item.content,
        layer: item.layer,
        category: item.category,
        createdAt: item.created_at,
        importance: item.importance,
        tags: item.tags,
    }))
}

export async function getActiveIntents(): Promise<Intent[]> {
    const res = await request("/intents", { headers: authHeaders() })
    const data = await res.json()

    return data.map((item: any) => ({
        id: item.id,
        description: item.description,
        priority: mapPriority(item.priority),
        status: item.status,
        createdAt: item.created_at,
    }))
}

export async function searchMemories(query: string, limit: number = 10): Promise<SearchResult[]> {
    const res = await request("/recall", {
        method: "POST",
        headers: jsonHeaders(),
        body: JSON.stringify({ query, limit }),
    })

    const data = await res.json()

    return data.map((item: any) => ({
        id: item.id,
        content: item.content,
        layer: item.layer,
        category: item.category,
        createdAt: item.created_at,
        similarity: item.similarity,
        importance: item.importance,
    }))
}

export async function getGraphData(): Promise<any> {
    const res = await request("/graph", { headers: authHeaders() })
    return res.json()
}

export async function createIntent(description: string, priority: number): Promise<Intent> {
    const res = await request("/intents", {
        method: "POST",
        headers: jsonHeaders(),
        body: JSON.stringify({ description, priority: toApiPriority(priority), context: {} })
    })

    const data = await res.json()

    return {
        id: data.id,
        description: data.description,
        priority: mapPriority(data.priority),
        status: data.status,
        createdAt: data.created_at
    }
}

export async function createMemory(content: string, category: string, tags: string[]): Promise<any> {
    const res = await request("/memories", {
        method: "POST",
        headers: jsonHeaders(),
        body: JSON.stringify({
            content,
            category,
            tags,
            layer: "episodic",
            importance: 0.5
        })
    })

    return res.json()
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
