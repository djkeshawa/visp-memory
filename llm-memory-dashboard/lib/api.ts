
import { Memory, Intent, Stats, SearchResult } from "./types"

const API_BASE_URL = process.env.NEXT_PUBLIC_LLM_MEMORY_API_URL || ""

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

export async function getStats(): Promise<Stats> {
    const res = await fetch(`${API_BASE_URL}/`, { headers: authHeaders() })
    if (!res.ok) throw new Error("Failed to fetch stats")
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
    const res = await fetch(`${API_BASE_URL}/memories?limit=${limit}`, { headers: authHeaders() })
    if (!res.ok) throw new Error("Failed to fetch memories")
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
    const res = await fetch(`${API_BASE_URL}/intents`, { headers: authHeaders() })
    if (!res.ok) throw new Error("Failed to fetch intents")
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
    const res = await fetch(`${API_BASE_URL}/recall`, {
        method: "POST",
        headers: jsonHeaders(),
        body: JSON.stringify({ query, limit }),
    })

    if (!res.ok) throw new Error("Failed to search memories")
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
    const res = await fetch(`${API_BASE_URL}/graph`, { headers: authHeaders() })
    if (!res.ok) throw new Error("Failed to fetch graph data")
    return res.json()
}

export async function createIntent(description: string, priority: number): Promise<Intent> {
    const res = await fetch(`${API_BASE_URL}/intents`, {
        method: "POST",
        headers: jsonHeaders(),
        body: JSON.stringify({ description, priority: toApiPriority(priority), context: {} })
    })

    if (!res.ok) throw new Error("Failed to create intent")
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
    const res = await fetch(`${API_BASE_URL}/memories`, {
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

    if (!res.ok) throw new Error("Failed to create memory")
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
