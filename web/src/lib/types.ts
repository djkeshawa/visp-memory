
// web/src/lib/types.ts

export interface Memory {
    id: string;
    content: string;
    layer: 'episodic' | 'semantic' | 'intent';
    category: string;
    importance: number;
    repo_id?: string;
    tags: string[];
    metadata: Record<string, any>;
    created_at: string;
    accessed_at: string;
}

export interface Intent {
    id: string;
    description: string;
    priority: number;
    status: string;
    context: Record<string, any>;
    created_at: string;
}

export interface Stats {
    total_memories: number;
    active_intents: number;
    total_relationships: number;
    memories_by_layer: Record<string, number>;
}
