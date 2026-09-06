"use client"

import { MemoryDetails } from "@/components/recall/memory-details"
import { useEffect, useRef, useState } from "react"
import { Archive, GitMerge, RefreshCw, RotateCcw, Trash2, Undo2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
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
import { MEMORY_LAYER_CONFIG, normalizeMemoryLayer } from "@/lib/layers"
import { useSelectedProjectId } from "@/lib/project-selection"
import type { Memory, MemoryMergePreview } from "@/lib/types"
import { cn } from "@/lib/utils"

const statuses = ["active", "archived", "deleted", "merged", "superseded"] as const

export default function MemoriesPage() {
  const repoId = useSelectedProjectId()
  const [status, setStatus] = useState<(typeof statuses)[number]>("active")
  const [memories, setMemories] = useState<Memory[]>([])
  const [selected, setSelected] = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [preview, setPreview] = useState<MemoryMergePreview | null>(null)
  const [mergeOpen, setMergeOpen] = useState(false)
  const [targetId, setTargetId] = useState("")
  const [lastOperation, setLastOperation] = useState<string | null>(null)
  const repoIdRef = useRef(repoId)
  const statusRef = useRef(status)
  const requestGenerationRef = useRef(0)
  repoIdRef.current = repoId
  statusRef.current = status

  const load = async () => {
    const requestedRepoId = repoId
    const requestedStatus = status
    if (repoIdRef.current !== requestedRepoId || statusRef.current !== requestedStatus) return
    const generation = ++requestGenerationRef.current
    setLoading(true)
    try {
      const nextMemories = await getMemories(requestedRepoId, requestedStatus)
      if (
        generation !== requestGenerationRef.current ||
        repoIdRef.current !== requestedRepoId ||
        statusRef.current !== requestedStatus
      ) return
      setMemories(nextMemories)
      setSelected([])
      setError(null)
    } catch (loadError) {
      if (
        generation !== requestGenerationRef.current ||
        repoIdRef.current !== requestedRepoId ||
        statusRef.current !== requestedStatus
      ) return
      setError(describeApiError(loadError))
    } finally {
      if (
        generation === requestGenerationRef.current &&
        repoIdRef.current === requestedRepoId &&
        statusRef.current === requestedStatus
      ) {
        setLoading(false)
      }
    }
  }

  useEffect(() => {
    ++requestGenerationRef.current
    setMemories([])
    setSelected([])
    setPreview(null)
    setMergeOpen(false)
    setLastOperation(null)
    setError(null)
    void load()
  }, [repoId, status])

  const toggle = (memoryId: string) => {
    setSelected((current) => current.includes(memoryId) ? current.filter((id) => id !== memoryId) : [...current, memoryId])
  }

  const openMerge = async () => {
    if (selected.length < 2) return
    const requestedRepoId = repoId
    const requestedStatus = status
    const canonical = targetId && selected.includes(targetId) ? targetId : selected[0]
    setTargetId(canonical)
    setMergeOpen(true)
    try {
      const nextPreview = await previewMemoryMerge(selected, canonical)
      if (repoIdRef.current !== requestedRepoId || statusRef.current !== requestedStatus) return
      setPreview(nextPreview)
    } catch (previewError) {
      if (repoIdRef.current !== requestedRepoId || statusRef.current !== requestedStatus) return
      setError(describeApiError(previewError))
      setMergeOpen(false)
    }
  }

  const refreshPreview = async (canonical: string) => {
    const requestedRepoId = repoId
    const requestedStatus = status
    setTargetId(canonical)
    try {
      const nextPreview = await previewMemoryMerge(selected, canonical)
      if (repoIdRef.current !== requestedRepoId || statusRef.current !== requestedStatus) return
      setPreview(nextPreview)
    } catch (previewError) {
      if (repoIdRef.current !== requestedRepoId || statusRef.current !== requestedStatus) return
      setError(describeApiError(previewError))
    }
  }

  const executeMerge = async () => {
    if (!preview || preview.validationErrors.length) return
    const requestedRepoId = repoId
    try {
      const result = await mergeMemories(selected, targetId, !preview.exactDuplicate)
      if (repoIdRef.current !== requestedRepoId) return
      setLastOperation(result.operationId || null)
      setMergeOpen(false)
      await load()
    } catch (mergeError) {
      if (repoIdRef.current !== requestedRepoId) return
      setError(describeApiError(mergeError))
    }
  }

  const remove = async (memory: Memory) => {
    const requestedRepoId = repoId
    try {
      await deleteMemory(memory.id)
      if (repoIdRef.current !== requestedRepoId) return
      await load()
    } catch (actionError) {
      if (repoIdRef.current !== requestedRepoId) return
      setError(describeApiError(actionError))
    }
  }

  const restore = async (memory: Memory) => {
    const requestedRepoId = repoId
    try {
      await restoreMemory(memory.id)
      if (repoIdRef.current !== requestedRepoId) return
      await load()
    } catch (actionError) {
      if (repoIdRef.current !== requestedRepoId) return
      setError(describeApiError(actionError))
    }
  }

  const purge = async (memory: Memory) => {
    if (!window.confirm(`Permanently purge memory ${memory.id}?`)) return
    const requestedRepoId = repoId
    try {
      await purgeMemory(memory.id)
      if (repoIdRef.current !== requestedRepoId) return
      await load()
    } catch (actionError) {
      if (repoIdRef.current !== requestedRepoId) return
      setError(describeApiError(actionError))
    }
  }

  const undo = async () => {
    if (!lastOperation) return
    const requestedRepoId = repoId
    try {
      await undoMemoryMerge(lastOperation)
      if (repoIdRef.current !== requestedRepoId) return
      setLastOperation(null)
      await load()
    } catch (undoError) {
      if (repoIdRef.current !== requestedRepoId) return
      setError(describeApiError(undoError))
    }
  }

  return (
    <div className="space-y-6">
      <header className="flex flex-col gap-3 border-b border-border pb-5 sm:flex-row sm:items-end sm:justify-between">
        <div><h1 className="text-2xl font-semibold text-foreground">Memories</h1><p className="mt-1 text-sm text-muted-foreground">Review content, provenance, duplicates, and lifecycle state.</p></div>
        <div className="flex gap-2">{lastOperation ? <Button size="sm" variant="outline" onClick={() => void undo()}><Undo2 className="h-4 w-4" /><span>Undo merge</span></Button> : null}<Button size="sm" variant="outline" onClick={() => void load()} disabled={loading}><RefreshCw className={loading ? "h-4 w-4 animate-spin" : "h-4 w-4"} /><span>Refresh</span></Button></div>
      </header>

      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex max-w-full gap-1 overflow-x-auto rounded-md border border-border bg-secondary/35 p-1" role="tablist" aria-label="Memory status">
          {statuses.map((item) => <button key={item} type="button" role="tab" aria-selected={status === item} onClick={() => setStatus(item)} className={status === item ? "rounded-sm bg-background px-3 py-1.5 text-sm font-medium text-foreground shadow-sm" : "rounded-sm px-3 py-1.5 text-sm text-muted-foreground hover:text-foreground"}>{item}</button>)}
        </div>
        {status === "active" ? <Button size="sm" onClick={() => void openMerge()} disabled={selected.length < 2}><GitMerge className="h-4 w-4" /><span>Merge selected ({selected.length})</span></Button> : null}
      </div>

      {error ? <p className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive" role="alert">{error}</p> : null}

      <div className="overflow-x-auto rounded-lg border border-border">
        <table className="w-full min-w-[54rem] text-left text-sm">
          <thead className="bg-secondary/60 text-xs text-muted-foreground"><tr><th className="w-12 px-4 py-3"><span className="sr-only">Select</span></th><th className="px-4 py-3">Memory</th><th className="px-4 py-3">Layer</th><th className="px-4 py-3">Category</th><th className="px-4 py-3">Created</th><th className="px-4 py-3 text-right">Actions</th></tr></thead>
          <tbody className="divide-y divide-border">
            {memories.map((memory) => {
              const layerConfig = MEMORY_LAYER_CONFIG[normalizeMemoryLayer(memory.layer)]
              return (
                <tr key={memory.id} className={selected.includes(memory.id) ? "bg-primary/5" : undefined}>
                  <td className="px-4 py-3"><input type="checkbox" checked={selected.includes(memory.id)} onChange={() => toggle(memory.id)} disabled={status !== "active"} aria-label={`Select memory ${memory.id}`} /></td>
                  <td className="max-w-xl px-4 py-3"><p className="line-clamp-2 text-foreground">{memory.content}</p><MemoryDetails memory={memory} /><div className="mt-1 flex flex-wrap gap-1">{memory.tags?.slice(0, 4).map((tag) => <span key={tag} className="rounded-sm bg-secondary px-1.5 py-0.5 text-[11px] text-muted-foreground">{tag}</span>)}</div><code className="mt-1 block text-[11px] text-muted-foreground">{memory.id}</code></td>
                  <td className="px-4 py-3"><span className={cn("inline-flex rounded-full px-2 py-0.5 text-xs font-medium", layerConfig.bgColor, layerConfig.color)}>{layerConfig.label}</span></td><td className="px-4 py-3 text-muted-foreground">{memory.category}</td><td className="px-4 py-3 text-muted-foreground">{new Date(memory.createdAt).toLocaleDateString()}</td>
                  <td className="px-4 py-3"><div className="flex justify-end gap-1">{status === "deleted" ? <Button size="sm" variant="ghost" title="Restore" onClick={() => void restore(memory)}><RotateCcw className="h-4 w-4" /></Button> : status === "active" || status === "archived" ? <Button size="sm" variant="ghost" title="Move to trash" onClick={() => void remove(memory)}><Archive className="h-4 w-4" /></Button> : null}{status !== "active" ? <Button size="sm" variant="ghost" title="Permanently purge" onClick={() => void purge(memory)}><Trash2 className="h-4 w-4 text-destructive" /></Button> : null}</div></td>
                </tr>
              )
            })}
            {!loading && memories.length === 0 ? <tr><td colSpan={6} className="px-4 py-10 text-center text-muted-foreground">No {status} memories in this project.</td></tr> : null}
          </tbody>
        </table>
      </div>

      <Dialog open={mergeOpen} onOpenChange={setMergeOpen}>
        <DialogContent className="sm:max-w-2xl">
          <DialogHeader><DialogTitle>Merge memories</DialogTitle><DialogDescription>Preserve one canonical memory and move the others into recoverable merged history.</DialogDescription></DialogHeader>
          {preview ? <div className="space-y-4"><div className="grid grid-cols-3 gap-3 rounded-md border border-border bg-secondary/40 p-3 text-sm"><div><p className="text-muted-foreground">Sources</p><p className="font-semibold">{preview.memoryIds.length}</p></div><div><p className="text-muted-foreground">Tokens saved</p><p className="font-semibold">{preview.estimatedTokensSaved}</p></div><div><p className="text-muted-foreground">Links retargeted</p><p className="font-semibold">{preview.relationshipRewrites}</p></div></div><label className="block space-y-2 text-sm"><span className="font-medium">Canonical memory</span><select value={targetId} onChange={(event) => void refreshPreview(event.target.value)} className="h-10 w-full rounded-md border border-input bg-background px-3">{selected.map((id) => <option key={id} value={id}>{id}</option>)}</select></label>{preview.validationErrors.length ? <ul className="space-y-1 rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">{preview.validationErrors.map((item) => <li key={item}>{item}</li>)}</ul> : null}{preview.warnings.length ? <ul className="space-y-1 rounded-md border border-intent/30 bg-intent/5 p-3 text-sm text-muted-foreground">{preview.warnings.map((item) => <li key={item}>{item}</li>)}</ul> : null}</div> : <p className="text-sm text-muted-foreground">Preparing merge preview...</p>}
          <DialogFooter><Button variant="outline" onClick={() => setMergeOpen(false)}>Cancel</Button><Button onClick={() => void executeMerge()} disabled={!preview || preview.validationErrors.length > 0}><GitMerge className="h-4 w-4" /><span>{preview?.exactDuplicate ? "Merge exact duplicates" : "Confirm reviewed merge"}</span></Button></DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
