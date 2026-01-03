
// web/src/lib/api.ts
import useSWR from 'swr';
import { Memory, Stats, Intent } from './types';

const API_BASE = 'http://localhost:8000';

export const fetcher = (url: string) => fetch(url).then((res) => res.json());

export function useStats() {
    const { data, error, isLoading } = useSWR<Stats>(`${API_BASE}/`, fetcher);
    return {
        stats: data,
        isLoading,
        isError: error
    };
}

export function useMemories() {
    const { data, error, isLoading } = useSWR<Memory[]>(`${API_BASE}/memories`, fetcher);
    return {
        memories: data,
        isLoading,
        isError: error
    };
}

export function useIntents() {
    const { data, error, isLoading } = useSWR<Intent[]>(`${API_BASE}/intents`, fetcher);
    return {
        intents: data,
        isLoading,
        isError: error
    };
}

export async function searchMemories(query: string, repo_id?: string) {
    const res = await fetch(`${API_BASE}/recall`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query, repo_id }),
    });
    return res.json();
}
