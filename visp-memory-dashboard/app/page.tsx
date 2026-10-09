"use client"

import { Suspense, useCallback, useEffect, useRef, useState } from "react"
import { AlertTriangle, RefreshCw } from "lucide-react"
import { ActiveIntents } from "@/components/dashboard/active-intents"
import { DeskSearch } from "@/components/dashboard/desk-search"
import { MemoryTotals } from "@/components/dashboard/memory-totals"
import { NeedsReview } from "@/components/dashboard/needs-review"
import { RecentActivity } from "@/components/dashboard/recent-activity"
import { SystemStatus } from "@/components/dashboard/system-status"
import { PageHeader } from "@/components/strata/primitives"
import { describeApiError, getStats, getRecentMemories } from "@/lib/api"
import { useProjectScopes, useSelectedProjectId } from "@/lib/project-selection"
import type { Memory, Stats } from "@/lib/types"

function DashboardContent() {
  const selectedRepoId = useSelectedProjectId()
  const [stats, setStats] = useState<Stats | null>(null)
  const [memories, setMemories] = useState<Memory[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const selectedRepoIdRef = useRef(selectedRepoId)
  const requestGenerationRef = useRef(0)
  selectedRepoIdRef.current = selectedRepoId

  const fetchData = useCallback(async () => {
    const requestedRepoId = selectedRepoId
    if (selectedRepoIdRef.current !== requestedRepoId) return
    const generation = ++requestGenerationRef.current
    setIsLoading(true)
    try {
      const [statsData, memoriesData] = await Promise.all([
        getStats(requestedRepoId),
        getRecentMemories(8, requestedRepoId),
      ])
      if (generation !== requestGenerationRef.current || selectedRepoIdRef.current !== requestedRepoId) return
      setStats(statsData)
      setMemories(memoriesData)
      setLoadError(null)
    } catch (error) {
      if (generation !== requestGenerationRef.current || selectedRepoIdRef.current !== requestedRepoId) return
      console.error("Failed to fetch dashboard data:", error)
      setLoadError(describeApiError(error))
    } finally {
      if (generation === requestGenerationRef.current && selectedRepoIdRef.current === requestedRepoId) {
        setIsLoading(false)
      }
    }
  }, [selectedRepoId])

  useEffect(() => {
    setStats(null)
    setMemories([])
    setLoadError(null)
    void fetchData()
  }, [fetchData])

  const { projects } = useProjectScopes()
  const projectName = projects.find((project) => project.id === selectedRepoId)?.name || selectedRepoId || "Your workspace"

  return (
    <div className="space-y-7">
      <DeskSearch repoId={selectedRepoId} onMemoryCreated={fetchData} />

      <PageHeader
        eyebrow={<span className="block max-w-lg truncate font-mono text-xs">{projectName}</span>}
        title="What this project knows"
        actions={
          <button type="button" onClick={() => void fetchData()} disabled={isLoading} aria-label="Refresh dashboard" className="flex h-11 w-11 items-center justify-center rounded-[10px] border border-border text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground disabled:opacity-50">
            <RefreshCw className={`h-4 w-4 ${isLoading ? "animate-spin" : ""}`} aria-hidden="true" />
          </button>
        }
      />

      {loadError && <div role="alert" className="flex flex-wrap items-center gap-3 rounded-xl border border-destructive/30 bg-destructive/5 p-4">
        <AlertTriangle className="h-5 w-5 shrink-0 text-destructive" aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium">Dashboard is not connected</p>
          <p className="mt-1 break-words text-sm text-muted-foreground">{loadError}</p>
        </div>
        <button type="button" onClick={() => void fetchData()} disabled={isLoading} className="h-10 rounded-lg border border-border bg-card px-3 text-sm font-medium disabled:opacity-50">{isLoading ? "Retrying…" : "Retry connection"}</button>
      </div>}

      <MemoryTotals stats={stats} repoId={selectedRepoId} isLoading={isLoading} unavailable={!!loadError} />

      <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1fr)_320px]">
        <RecentActivity key={selectedRepoId} memories={memories} isLoading={isLoading} unavailable={!!loadError} />
        <aside className="grid gap-6 md:grid-cols-2 xl:grid-cols-1">
          <NeedsReview repoId={selectedRepoId} />
          <ActiveIntents repoId={selectedRepoId} />
          <SystemStatus />
        </aside>
      </div>
      <footer className="border-t border-border pb-2 pt-5 text-xs leading-5 text-muted-foreground">Memory supplies context and cited knowledge. It does not grant permission or decide readiness.</footer>
    </div>
  )
}

export default function DashboardPage() {
  return <Suspense fallback={null}>
    <DashboardContent />
  </Suspense>
}
