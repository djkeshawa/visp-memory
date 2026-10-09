"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import {
  deleteMemory,
  describeApiError,
  getMemories,
  mergeMemories,
  previewMemoryMerge,
  purgeMemory,
  restoreMemory,
  undoMemoryMerge,
} from "@/lib/api"
import { useSelectedProjectId } from "@/lib/project-selection"
import type { Memory, MemoryMergePreview } from "@/lib/types"

export const LIBRARY_STATUSES = [
  { value: "active", label: "Active" },
  { value: "pending", label: "Pending" },
  { value: "archived", label: "Archived" },
  { value: "merged", label: "Merged" },
  { value: "superseded", label: "Superseded" },
  { value: "deleted", label: "Trash" },
] as const

export type LibraryStatus = (typeof LIBRARY_STATUSES)[number]["value"]

export const PAGE_SIZE = 50

function mergePreviewKey(view: string, memoryIds: string[], targetId: string) {
  return JSON.stringify([view, memoryIds, targetId])
}

/**
 * Library state: one page of memories for the selected project and status, the selection,
 * the merge preview, and the lifecycle actions. Every async result is checked against the
 * view it was requested for, so a slow response never lands in a different project or page.
 */
export function useLibrary() {
  const repoId = useSelectedProjectId()
  const [status, setStatus] = useState<LibraryStatus>("active")
  const [memories, setMemories] = useState<Memory[]>([])
  const [selected, setSelected] = useState<string[]>([])
  const [page, setPage] = useState({ scope: "", index: 0 })
  const [hasNextPage, setHasNextPage] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [previewResult, setPreviewResult] = useState<{ key: string; value: MemoryMergePreview } | null>(null)
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [mergeOpen, setMergeOpen] = useState(false)
  const [merging, setMerging] = useState(false)
  const [targetId, setTargetId] = useState("")
  const [lastOperation, setLastOperation] = useState<string | null>(null)
  const [trashing, setTrashing] = useState(false)
  const scopeKey = JSON.stringify([repoId, status])
  const pageIndex = page.scope === scopeKey ? page.index : 0
  const viewKey = JSON.stringify([scopeKey, pageIndex])
  const viewKeyRef = useRef(viewKey)
  const repoIdRef = useRef(repoId)
  const requestGenerationRef = useRef(0)
  const previewGenerationRef = useRef(0)
  const mergeGenerationRef = useRef(0)
  repoIdRef.current = repoId
  viewKeyRef.current = viewKey
  const preview = previewResult?.key === mergePreviewKey(viewKey, selected, targetId) ? previewResult.value : null

  useEffect(() => setPage({ scope: scopeKey, index: 0 }), [scopeKey])

  const load = async () => {
    if (viewKeyRef.current !== viewKey) return
    const generation = ++requestGenerationRef.current
    const isCurrent = () => generation === requestGenerationRef.current && viewKeyRef.current === viewKey
    setLoading(true)
    try {
      // One extra record establishes whether another page exists without fetching the library.
      const nextMemories = await getMemories(repoId, status, PAGE_SIZE + 1, pageIndex * PAGE_SIZE)
      if (!isCurrent()) return
      if (!nextMemories.length && pageIndex > 0) {
        setPage({ scope: scopeKey, index: pageIndex - 1 })
        return
      }
      setMemories(nextMemories.slice(0, PAGE_SIZE))
      setHasNextPage(nextMemories.length > PAGE_SIZE)
      setSelected([])
      setError(null)
    } catch (loadError) {
      if (!isCurrent()) return
      setError(describeApiError(loadError))
    } finally {
      if (isCurrent()) setLoading(false)
    }
  }

  useEffect(() => {
    ++requestGenerationRef.current
    ++previewGenerationRef.current
    ++mergeGenerationRef.current
    setMemories([])
    setSelected([])
    setHasNextPage(false)
    setPreviewResult(null)
    setPreviewError(null)
    setMergeOpen(false)
    setMerging(false)
    setTrashing(false)
    setError(null)
    void load()
  }, [viewKey])

  useEffect(() => setLastOperation(null), [repoId, status])

  const toggle = (memoryId: string) => {
    setSelected((current) => current.includes(memoryId) ? current.filter((id) => id !== memoryId) : [...current, memoryId])
  }
  /** Drops selected ids the reader can no longer see, so bulk actions never touch hidden rows. */
  const keepOnly = useCallback((visibleIds: ReadonlySet<string>) => {
    setSelected((current) => {
      const next = current.filter((id) => visibleIds.has(id))
      return next.length === current.length ? current : next
    })
  }, [])


  const refreshPreview = async (canonical: string) => {
    const generation = ++previewGenerationRef.current
    const key = mergePreviewKey(viewKey, selected, canonical)
    const isCurrent = () => generation === previewGenerationRef.current && viewKeyRef.current === viewKey
    setTargetId(canonical)
    setPreviewResult(null)
    setPreviewError(null)
    try {
      const nextPreview = await previewMemoryMerge(selected, canonical)
      if (isCurrent()) setPreviewResult({ key, value: nextPreview })
    } catch (failure) {
      if (isCurrent()) setPreviewError(describeApiError(failure))
    }
  }

  const openMerge = () => {
    if (selected.length < 2) return
    setMergeOpen(true)
    void refreshPreview(targetId && selected.includes(targetId) ? targetId : selected[0])
  }

  const closeMerge = () => {
    ++previewGenerationRef.current
    setPreviewResult(null)
    setMergeOpen(false)
  }

  const executeMerge = async () => {
    if (!preview || preview.validationErrors.length || merging) return
    const generation = ++mergeGenerationRef.current
    const isCurrent = () => generation === mergeGenerationRef.current && viewKeyRef.current === viewKey
    setMerging(true)
    try {
      const result = await mergeMemories(preview.memoryIds, preview.targetId, !preview.exactDuplicate)
      if (!isCurrent()) return
      setLastOperation(result.operationId || null)
      closeMerge()
      await load()
    } catch (mergeError) {
      if (!isCurrent()) return
      setPreviewError(describeApiError(mergeError))
    } finally {
      if (isCurrent()) setMerging(false)
    }
  }

  /** Runs a lifecycle action, then reloads, unless the project changed while it was running. */
  const act = async (run: () => Promise<void>, afterSuccess?: () => void) => {
    const requestedRepoId = repoId
    try {
      await run()
      if (repoIdRef.current !== requestedRepoId) return
      afterSuccess?.()
      await load()
    } catch (actionError) {
      if (repoIdRef.current !== requestedRepoId) return
      setError(describeApiError(actionError))
    }
  }

  const remove = (memory: Memory) => act(() => deleteMemory(memory.id))
  /** Moves every selected memory to recoverable trash; reports how many could not be moved. */
  const removeSelected = async () => {
    if (!selected.length || trashing) return
    const ids = selected
    const requestedRepoId = repoId
    const requestedView = viewKey
    setTrashing(true)
    const results = await Promise.allSettled(ids.map((id) => deleteMemory(id)))
    if (repoIdRef.current !== requestedRepoId || viewKeyRef.current !== requestedView) return
    const failures = results.filter((result): result is PromiseRejectedResult => result.status === "rejected")
    await load()
    if (repoIdRef.current !== requestedRepoId || viewKeyRef.current !== requestedView) return
    setTrashing(false)
    if (failures.length) {
      setError(`${failures.length} of ${ids.length} memories could not be moved to trash: ${describeApiError(failures[0].reason)}`)
    }
  }
  const restore = (memory: Memory) => act(() => restoreMemory(memory.id))
  const purge = async (memory: Memory) => {
    if (!window.confirm(`Permanently purge memory ${memory.id}?`)) return
    await act(() => purgeMemory(memory.id))
  }
  const undo = async () => {
    if (!lastOperation) return
    const operationId = lastOperation
    await act(() => undoMemoryMerge(operationId), () => setLastOperation(null))
  }

  return {
    status, setStatus, memories, selected, toggle, keepOnly, loading, error, hasNextPage, pageIndex,
    goToPage: (index: number) => setPage({ scope: scopeKey, index }),
    reload: () => load(),
    merge: { open: mergeOpen, merging, targetId, preview, previewError, openMerge, closeMerge, executeMerge, refreshPreview },
    remove, removeSelected, trashing, restore, purge, undo, lastOperation,
  }
}

export type Library = ReturnType<typeof useLibrary>
