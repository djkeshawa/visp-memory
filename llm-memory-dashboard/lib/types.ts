export type MemoryLayer = "episodic" | "semantic" | "intent"

export interface Memory {
  id: string
  content: string
  layer: MemoryLayer
  category: string
  createdAt: string
  importance?: number
  tags?: string[]
}

export interface SearchResult extends Memory {
  similarity?: number
}

export interface Intent {
  id: string
  description: string
  priority: "high" | "medium" | "low"
  status: "active" | "completed"
  createdAt: string
}

export interface Stats {
  totalMemories: number
  activeIntents: number
  knowledgeNodes: number
  connections: number
}

export interface SystemStatus {
  apiServer: "online" | "offline"
  vectorDatabase: "ready" | "syncing" | "offline"
  embeddings: "active" | "inactive"
}
