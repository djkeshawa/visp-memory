"use client"

import { Suspense, useCallback, useEffect, useRef, useState } from "react"
import Link from "next/link"
import { AlertTriangle, ArrowRight, ArrowUpRight, BookOpen, Brain, Database, RefreshCw, Search, Share2, Target } from "lucide-react"
import { RecentActivity } from "@/components/dashboard/recent-activity"
import { QuickActions } from "@/components/dashboard/quick-actions"
import { SystemStatus } from "@/components/dashboard/system-status"
import { MemoryGuide } from "@/components/dashboard/memory-guide"
import { describeApiError, getStats, getRecentMemories } from "@/lib/api"
import { projectHref, useProjectScopes, useSelectedProjectId } from "@/lib/project-selection"
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
  const metrics = [
    { label: "Stored memories", value: stats?.totalMemories, icon: Database, href: "/memories", description: "Across memory layers" },
    { label: "Knowledge nodes", value: stats?.knowledgeNodes, icon: Brain, href: "/graph", description: "In the semantic layer" },
    { label: "Connections", value: stats?.connections, icon: Share2, href: "/graph", description: "Relationships in the graph" },
    { label: "Active intents", value: stats?.activeIntents, icon: Target, href: "/intents", description: "Recorded work in progress" },
  ]

  return (
    <div className="space-y-8">
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div className="min-w-0 flex-1 basis-48">
          <p className="mb-2 max-w-lg truncate text-xs font-medium text-muted-foreground">{projectName}</p>
          <h1 className="font-semibold tracking-tight">Memory overview</h1>
          <p className="mt-2 text-sm text-muted-foreground">Your project’s knowledge, all in one place.</p>
        </div>
        <div className="flex items-center gap-2">
          <button type="button" onClick={() => void fetchData()} disabled={isLoading} aria-label="Refresh dashboard" className="flex h-10 w-10 items-center justify-center rounded-full border border-border bg-card text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground disabled:opacity-50">
            <RefreshCw className={`h-4 w-4 ${isLoading ? "animate-spin" : ""}`} />
          </button>
          <QuickActions onMemoryCreated={fetchData} />
        </div>
      </header>

      <section aria-labelledby="recall-heading" className="surface rounded-3xl p-6 sm:p-8">
        <div className="grid items-center gap-7 lg:grid-cols-[1fr_1.1fr] lg:gap-10">
          <div>
            <p className="mb-3 text-xs font-semibold text-primary">A place for what you know</p>
            <h2 id="recall-heading" className="max-w-md text-2xl font-semibold leading-snug sm:text-[28px]">The right context.<br />Right when you need it.</h2>
            <p className="mt-3 max-w-sm text-sm leading-6 text-muted-foreground">Revisit a decision, find a lesson, or pick up where you left off.</p>
          </div>
          <div className="space-y-3">
            <Link href={projectHref("/recall", selectedRepoId)} className="recall-entry group flex items-center gap-4 rounded-2xl border border-border bg-background/50 p-5 transition-all hover:border-primary/40 hover:bg-card">
              <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-accent text-primary">
                <Search className="h-5 w-5" />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-sm font-semibold">Recall a memory</span>
                <span className="mt-1 block text-xs leading-5 text-muted-foreground">Search in your own words</span>
              </span>
              <ArrowRight className="h-4 w-4 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-1 group-hover:text-primary" />
            </Link>
            <Link href={projectHref("/brief", selectedRepoId)} className="flex items-center gap-3 rounded-xl px-5 py-3 text-xs text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground">
              <BookOpen className="h-4 w-4 shrink-0" />
              <span>Gather context for your next task</span>
              <ArrowUpRight className="ml-auto h-4 w-4 shrink-0" />
            </Link>
          </div>
        </div>
      </section>

      {loadError && <div role="alert" className="flex flex-wrap items-center gap-3 rounded-xl border border-destructive/30 bg-destructive/5 p-4">
        <AlertTriangle className="h-5 w-5 shrink-0 text-destructive" />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium">Dashboard is not connected</p>
          <p className="mt-1 break-words text-sm text-muted-foreground">{loadError}</p>
        </div>
        <button type="button" onClick={() => void fetchData()} disabled={isLoading} className="rounded-md border border-border bg-card px-3 py-2 text-sm font-medium disabled:opacity-50">{isLoading ? "Retrying…" : "Retry connection"}</button>
      </div>}

      <section aria-label="Memory totals" aria-busy={isLoading} className="grid grid-cols-2 gap-3 lg:grid-cols-4 sm:gap-4">
        {metrics.map((metric) => <Link key={metric.label} href={projectHref(metric.href, selectedRepoId)} className="surface group rounded-2xl p-4 transition-colors hover:border-primary/40 sm:p-5">
          <div className="flex items-center justify-between gap-2 text-muted-foreground">
            <span className="text-xs font-medium">{metric.label}</span>
            <metric.icon className="h-4 w-4 shrink-0" />
          </div>
          <p className="mt-4 text-[32px] font-medium tabular-nums tracking-tight group-hover:text-primary">{isLoading || loadError ? "—" : metric.value?.toLocaleString() ?? "—"}</p>
          <p className="mt-1 text-[11px] leading-5 text-muted-foreground">{isLoading ? "Loading…" : loadError ? "Unavailable" : metric.description}</p>
        </Link>)}
      </section>

      <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1fr)_288px]">
        <RecentActivity key={selectedRepoId} memories={memories} isLoading={isLoading} unavailable={!!loadError} />
        <aside className="grid gap-6 md:grid-cols-2 xl:grid-cols-1">
          <MemoryGuide repoId={selectedRepoId} />
          <SystemStatus />
        </aside>
      </div>
      <footer className="border-t border-border/70 pt-5 pb-2 text-[11px] leading-5 text-muted-foreground">Memory supplies context and cited knowledge. It does not grant permission or decide readiness.</footer>
    </div>
  )
}

export default function DashboardPage() {
  return <Suspense fallback={null}>
    <DashboardContent />
  </Suspense>
}
