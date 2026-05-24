"use client"

import { useSearchParams } from "next/navigation"
import { useEffect, useState } from "react"

export const PROJECT_QUERY_PARAM = "repo_id"
export const PROJECT_STORAGE_KEY = "llm-memory-selected-repo-id"

export function useSelectedProjectId(): string | null {
  const searchParams = useSearchParams()
  const queryRepoId = searchParams.get(PROJECT_QUERY_PARAM)
  const [storedRepoId, setStoredRepoId] = useState<string | null>(null)

  useEffect(() => {
    setStoredRepoId(window.localStorage.getItem(PROJECT_STORAGE_KEY))
  }, [queryRepoId])

  return queryRepoId || storedRepoId
}

export function projectHref(path: string, repoId?: string | null): string {
  if (!repoId) return path

  const params = new URLSearchParams()
  params.set(PROJECT_QUERY_PARAM, repoId)
  return `${path}?${params.toString()}`
}
