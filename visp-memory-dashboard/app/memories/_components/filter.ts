import type { Memory } from "@/lib/types"

/** Case-insensitive match on the text, id, category, tags and files of a loaded memory. */
export function memoryMatchesQuery(memory: Memory, query: string): boolean {
  const needle = query.trim().toLowerCase()
  if (!needle) return true
  return [memory.content, memory.id, memory.category, ...(memory.tags ?? []), ...(memory.files ?? [])]
    .some((value) => value?.toLowerCase().includes(needle))
}
