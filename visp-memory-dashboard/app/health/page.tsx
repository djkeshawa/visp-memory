"use client"

import { Suspense, useEffect, useMemo, useRef, useState } from "react"
import type React from "react"
import { motion } from "framer-motion"
import { Activity, AlertTriangle, RefreshCw, TrendingDown } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { describeApiError, getDecayPreview } from "@/lib/api"
import { pageTransition } from "@/lib/animations"
import { useSelectedProjectId } from "@/lib/project-selection"
import type { DecayPreviewItem, DecayPreviewResponse } from "@/lib/types"
import { cn } from "@/lib/utils"

const riskConfig: Record<DecayPreviewItem["risk"], { label: string; className: string }> = {
  likely_to_decay: {
    label: "Likely to decay",
    className: "bg-red-50 text-red-700 dark:bg-red-950/40 dark:text-red-300",
  },
  weakening: {
    label: "Weakening",
    className: "bg-amber-50 text-amber-700 dark:bg-amber-950/40 dark:text-amber-300",
  },
  at_floor: {
    label: "At floor",
    className: "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300",
  },
  stable: {
    label: "Stable",
    className: "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300",
  },
}

function percent(value: number): string {
  return `${Math.round(value * 100)}%`
}

function formatDays(value: number): string {
  if (value >= 365) return `${(value / 365).toFixed(1)}y`
  return `${Math.round(value)}d`
}

function HealthContent() {
  const selectedRepoId = useSelectedProjectId()
  const [preview, setPreview] = useState<DecayPreviewResponse | null>(null)
  const [halflifeDays, setHalflifeDays] = useState(30)
  const [limit, setLimit] = useState(25)
  const [isLoading, setIsLoading] = useState(true)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const selectedRepoIdRef = useRef(selectedRepoId)
  const requestGenerationRef = useRef(0)
  selectedRepoIdRef.current = selectedRepoId

  const loadPreview = async () => {
    const requestedRepoId = selectedRepoId
    const generation = ++requestGenerationRef.current
    setIsLoading(true)
    try {
      const data = await getDecayPreview({
        repoId: requestedRepoId,
        limit,
        halflifeDays,
        minImportance: 0.1,
      })
      if (generation !== requestGenerationRef.current || selectedRepoIdRef.current !== requestedRepoId) return
      setPreview(data)
      setErrorMessage(null)
    } catch (error) {
      if (generation !== requestGenerationRef.current || selectedRepoIdRef.current !== requestedRepoId) return
      console.error("Failed to load memory health", error)
      setErrorMessage(describeApiError(error))
    } finally {
      if (generation === requestGenerationRef.current && selectedRepoIdRef.current === requestedRepoId) {
        setIsLoading(false)
      }
    }
  }

  useEffect(() => {
    setPreview(null)
    setErrorMessage(null)
    void loadPreview()
  }, [selectedRepoId])

  const summary = useMemo(() => {
    const candidates = preview?.candidates || []
    const likely = candidates.filter((item) => item.risk === "likely_to_decay").length
    const weakening = candidates.filter((item) => item.risk === "weakening").length
    const averageStrength =
      candidates.length > 0
        ? candidates.reduce((total, item) => total + item.currentImportance, 0) / candidates.length
        : 0

    return { likely, weakening, averageStrength }
  }, [preview])

  return (
    <motion.div initial="initial" animate="animate" variants={pageTransition} className="space-y-8">
      <div className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-foreground">Memory Health</h1>
          <p className="mt-1 text-muted-foreground">
            {selectedRepoId ? `Project: ${selectedRepoId}` : "Strength, decay risk, and lifespan signals"}
          </p>
        </div>
        <div className="flex flex-wrap items-end gap-3">
          <div className="grid gap-1.5">
            <Label htmlFor="halflife" className="text-xs">Half-life days</Label>
            <Input
              id="halflife"
              type="number"
              min={1}
              max={3650}
              value={halflifeDays}
              onChange={(event) => setHalflifeDays(Number(event.target.value) || 30)}
              className="h-9 w-28"
            />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="limit" className="text-xs">Limit</Label>
            <Input
              id="limit"
              type="number"
              min={1}
              max={100}
              value={limit}
              onChange={(event) => setLimit(Number(event.target.value) || 25)}
              className="h-9 w-24"
            />
          </div>
          <Button onClick={loadPreview} disabled={isLoading} className="h-9">
            <RefreshCw className={cn("mr-2 h-4 w-4", isLoading && "animate-spin")} />
            Refresh
          </Button>
        </div>
      </div>

      {errorMessage ? (
        <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-4 text-sm text-muted-foreground">
          <div className="flex items-start gap-2">
            <AlertTriangle className="mt-0.5 h-4 w-4 text-destructive" />
            <span>{errorMessage}</span>
          </div>
        </div>
      ) : null}

      <div className="grid gap-4 md:grid-cols-3">
        <Metric title="Average Strength" value={percent(summary.averageStrength)} icon={<Activity className="h-4 w-4" />} />
        <Metric title="Likely To Decay" value={summary.likely} icon={<TrendingDown className="h-4 w-4" />} />
        <Metric title="Weakening" value={summary.weakening} icon={<AlertTriangle className="h-4 w-4" />} />
      </div>

      <div className="glass overflow-hidden rounded-lg">
        <div className="border-b border-border p-5">
          <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
            <div>
              <h2 className="text-lg font-semibold text-foreground">Decay Preview</h2>
              <p className="mt-1 text-sm text-muted-foreground">
                Half-life {preview?.halflifeDays ?? halflifeDays}d, floor {percent(preview?.minImportance ?? 0.1)}
              </p>
            </div>
            <span
              className={cn(
                "inline-flex w-fit rounded-full px-2.5 py-1 text-xs font-medium",
                preview?.decayEnabled
                  ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300"
                  : "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300",
              )}
            >
              {preview?.decayEnabled ? "Decay enabled" : "Decay disabled"}
            </span>
          </div>
        </div>

        {isLoading ? (
          <div className="p-6 text-sm text-muted-foreground">Loading memory health...</div>
        ) : preview?.candidates.length ? (
          <div className="divide-y divide-border">
            {preview.candidates.map((item) => (
              <HealthRow key={item.memoryId} item={item} />
            ))}
          </div>
        ) : (
          <div className="p-6 text-sm text-muted-foreground">No active memories found.</div>
        )}
      </div>
    </motion.div>
  )
}

function Metric({ title, value, icon }: { title: string; value: string | number; icon: React.ReactNode }) {
  return (
    <div className="glass rounded-lg p-5">
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">{title}</p>
        <div className="rounded-lg bg-secondary p-2 text-muted-foreground">{icon}</div>
      </div>
      <p className="mt-3 text-2xl font-semibold text-foreground">{value}</p>
    </div>
  )
}

function HealthRow({ item }: { item: DecayPreviewItem }) {
  const risk = riskConfig[item.risk]

  return (
    <div className="grid gap-4 p-4 md:grid-cols-[minmax(0,1fr)_220px] md:items-center">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <span className={cn("rounded-full px-2.5 py-0.5 text-xs font-medium", risk.className)}>
            {risk.label}
          </span>
          <span className="text-xs text-muted-foreground">{item.layer}</span>
          {item.category ? <span className="text-xs text-muted-foreground">{item.category}</span> : null}
          <span className="text-xs text-muted-foreground">idle {formatDays(item.ageDays)}</span>
        </div>
        <p className="mt-2 line-clamp-2 text-sm text-foreground">{item.snippet}</p>
        <p className="mt-1 text-xs text-muted-foreground">{item.reason}</p>
      </div>
      <div className="space-y-2">
        <div className="flex items-center justify-between text-xs text-muted-foreground">
          <span>Strength</span>
          <span>{percent(item.currentImportance)} to {percent(item.projectedImportance)}</span>
        </div>
        <div className="h-2 rounded-full bg-secondary">
          <div
            className="h-2 rounded-full bg-primary"
            style={{ width: `${Math.max(4, Math.min(100, item.currentImportance * 100))}%` }}
          />
        </div>
        <div className="flex items-center justify-between text-xs text-muted-foreground">
          <span>Drop {percent(item.decayAmount)}</span>
          <span>{item.accessCount} accesses</span>
        </div>
      </div>
    </div>
  )
}

export default function HealthPage() {
  return (
    <Suspense fallback={null}>
      <HealthContent />
    </Suspense>
  )
}
