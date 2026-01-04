
import { Memory, Intent, Stats, SearchResult } from "./types"

const API_BASE_URL = "http://localhost:8000"

export async function getStats(): Promise<Stats> {
    const res = await fetch(`${API_BASE_URL}/`)
    if (!res.ok) throw new Error("Failed to fetch stats")
    const data = await res.json()

    return {
        totalMemories: data.total_memories || 0,
        activeIntents: data.active_intents || 0,
        knowledgeNodes: data.memories_by_layer?.semantic || 0, // Approx
        connections: data.total_relationships || 0,
    }
}

export async function getRecentMemories(limit: number = 8): Promise<Memory[]> {
    const res = await fetch(`${API_BASE_URL}/memories?limit=${limit}`)
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
    const res = await fetch(`${API_BASE_URL}/intents`)
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
        headers: {
            "Content-Type": "application/json",
        },
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
    const res = await fetch(`${API_BASE_URL}/graph`)
    if (!res.ok) throw new Error("Failed to fetch graph data")
    return res.json()
}

export async function createIntent(description: string, priority: number): Promise<Intent> {
    const res = await fetch(`${API_BASE_URL}/intents`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ description, priority: priority / 10, context: {} })
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
        headers: { "Content-Type": "application/json" },
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
    if (priority >= 7) return "high"
    if (priority >= 4) return "medium"
    return "low"
}
