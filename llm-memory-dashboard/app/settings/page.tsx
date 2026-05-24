"use client"

import { Suspense, useEffect, useState } from "react"
import { motion } from "framer-motion"
import { AlertTriangle, Database, RefreshCw, Settings, Wrench } from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  describeApiError,
  getEmbeddingIndexStatus,
  getProviderDiagnostics,
  reindexEmbeddingIndex,
  testProvider,
} from "@/lib/api"
import { pageTransition } from "@/lib/animations"
import { cn } from "@/lib/utils"
import { useSelectedProjectId } from "@/lib/project-selection"
import type {
  EmbeddingIndexStatus,
  EmbeddingReindexResult,
  ProviderConnectionStatus,
  ProviderDiagnostic,
} from "@/lib/types"

const statusConfig: Record<
  ProviderConnectionStatus,
  { label: string; pill: string; dot: string; text: string; border: string }
> = {
  connected: {
    label: "Connected",
    pill: "bg-success/15 text-success border-success/30",
    dot: "bg-success",
    text: "text-success",
    border: "border-success/30",
  },
  failed: {
    label: "Failed",
    pill: "bg-error/15 text-error border-error/30",
    dot: "bg-error",
    text: "text-error",
    border: "border-error/30",
  },
  disabled: {
    label: "Disabled",
    pill: "bg-muted text-muted-foreground border-border",
    dot: "bg-muted-foreground",
    text: "text-muted-foreground",
    border: "border-muted",
  },
  fallback: {
    label: "Fallback",
    pill: "bg-intent/15 text-intent border-intent/30",
    dot: "bg-intent",
    text: "text-intent",
    border: "border-intent/40",
  },
  not_configured: {
    label: "Not Configured",
    pill: "bg-muted/60 text-muted-foreground border-border",
    dot: "bg-muted-foreground",
    text: "text-muted-foreground",
    border: "border-border",
  },
  not_checked: {
    label: "Not Checked",
    pill: "bg-muted/60 text-muted-foreground border-border",
    dot: "bg-muted-foreground",
    text: "text-muted-foreground",
    border: "border-border",
  },
}

function SettingsContent() {
  const selectedRepoId = useSelectedProjectId()
  const [providers, setProviders] = useState<ProviderDiagnostic[]>([])
  const [embeddingIndex, setEmbeddingIndex] = useState<EmbeddingIndexStatus | null>(null)
  const [reindexResult, setReindexResult] = useState<EmbeddingReindexResult | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [testingProvider, setTestingProvider] = useState<string | null>(null)
  const [isReindexing, setIsReindexing] = useState(false)

  useEffect(() => {
    void loadDiagnostics()
  }, [selectedRepoId])

  const loadDiagnostics = async () => {
    setIsLoading(true)
    try {
      const [providerData, indexData] = await Promise.all([
        getProviderDiagnostics(),
        getEmbeddingIndexStatus(selectedRepoId),
      ])
      setProviders(providerData)
      setEmbeddingIndex(indexData)
      setLoadError(null)
    } catch (error) {
      console.error("Failed to fetch diagnostics:", error)
      setLoadError(describeApiError(error))
    } finally {
      setIsLoading(false)
    }
  }

  const handleReindex = async (dryRun: boolean) => {
    setIsReindexing(true)
    try {
      const result = await reindexEmbeddingIndex({ repoId: selectedRepoId, dryRun })
      setReindexResult(result)
      const updatedIndex = await getEmbeddingIndexStatus(selectedRepoId)
      setEmbeddingIndex(updatedIndex)
      setLoadError(null)
    } catch (error) {
      setLoadError(describeApiError(error))
    } finally {
      setIsReindexing(false)
    }
  }

  const handleTest = async (providerName: string) => {
    setTestingProvider(providerName)
    try {
      const result = await testProvider(providerName)
      setProviders((current) => upsertProvider(current, result))
    } catch (error) {
      const message = describeApiError(error)
      setProviders((current) =>
        upsertProvider(current, {
          provider: providerName,
          status: "failed",
          message,
          lastChecked: new Date().toISOString(),
        }),
      )
    } finally {
      setTestingProvider(null)
    }
  }

  return (
    <motion.div initial="initial" animate="animate" variants={pageTransition} className="space-y-8">
      <div className="flex items-center gap-3">
        <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-gradient-to-br from-blue-500 to-indigo-600">
          <Settings className="h-6 w-6 text-white" />
        </div>
        <div>
          <h1 className="text-2xl font-semibold text-foreground">Settings</h1>
          <p className="text-muted-foreground mt-1">Provider diagnostics and connectivity checks</p>
        </div>
      </div>

      {loadError ? (
        <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-4 text-sm text-muted-foreground">
          <div className="flex items-start gap-2">
            <AlertTriangle className="mt-0.5 h-4 w-4 text-destructive" />
            <span>{loadError}</span>
          </div>
        </div>
      ) : null}

      {isLoading ? (
        <div className="rounded-xl border border-border bg-card/80 p-4 text-sm text-muted-foreground">
          Loading diagnostics...
        </div>
      ) : null}

      {embeddingIndex ? (
        <div
          className={cn(
            "glass rounded-xl border p-4",
            embeddingIndex.needsReindex ? "border-intent/40" : "border-border",
          )}
        >
          <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
            <div className="min-w-0 flex-1 space-y-4">
              <div className="flex flex-wrap items-center gap-2">
                <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-primary/10">
                  <Database className="h-5 w-5 text-primary" />
                </div>
                <div>
                  <h2 className="text-lg font-semibold text-foreground">Embedding Index</h2>
                  <p className="text-sm text-muted-foreground">{embeddingIndex.message}</p>
                </div>
              </div>

              <div className="grid gap-3 text-sm md:grid-cols-4">
                <Metric label="Status" value={formatStatus(embeddingIndex.status)} />
                <Metric label="Matched" value={String(embeddingIndex.matchedMemories)} />
                <Metric
                  label="Indexed"
                  value={
                    typeof embeddingIndex.indexedMemories === "number"
                      ? String(embeddingIndex.indexedMemories)
                      : "Unknown"
                  }
                />
                <Metric
                  label="Dimension"
                  value={
                    typeof embeddingIndex.dimension === "number"
                      ? String(embeddingIndex.dimension)
                      : "Unknown"
                  }
                />
              </div>

              {embeddingIndex.legacyCollections.length > 0 ? (
                <div className="rounded-lg border border-intent/30 bg-intent/5 p-3 text-sm text-muted-foreground">
                  <div className="flex items-start gap-2">
                    <AlertTriangle className="mt-0.5 h-4 w-4 text-intent" />
                    <span>
                      Legacy vector collections detected: {embeddingIndex.legacyCollections.join(", ")}
                    </span>
                  </div>
                </div>
              ) : null}

              {reindexResult ? (
                <div className="rounded-lg border border-border bg-background/40 p-3 text-sm text-muted-foreground">
                  {reindexResult.message} Matched {reindexResult.matchedMemories}; rebuilt{" "}
                  {reindexResult.reindexedMemories}; failed {reindexResult.failedMemories}.
                </div>
              ) : null}
            </div>

            <div className="flex flex-wrap gap-2">
              <Button
                size="sm"
                variant="outline"
                onClick={() => handleReindex(true)}
                disabled={isReindexing}
              >
                <Wrench className={cn("h-4 w-4", isReindexing && "animate-pulse")} />
                <span>Dry Run</span>
              </Button>
              <Button
                size="sm"
                onClick={() => handleReindex(false)}
                disabled={isReindexing || embeddingIndex.status !== "available"}
              >
                <RefreshCw className={cn("h-4 w-4", isReindexing && "animate-spin")} />
                <span>Rebuild</span>
              </Button>
            </div>
          </div>
        </div>
      ) : null}

      {!isLoading && providers.length === 0 && !loadError ? (
        <div className="rounded-xl border border-border bg-card/80 p-4 text-sm text-muted-foreground">
          No provider diagnostics are currently available.
        </div>
      ) : null}

      {providers.length > 0 ? (
        <div className="space-y-4">
          {providers.map((provider) => {
            const config = statusConfig[provider.status]
            const lastChecked = formatLastChecked(provider.lastChecked)

            return (
              <div
                key={provider.provider}
                className={cn(
                  "glass rounded-xl border p-4",
                  testingProvider === provider.provider ? "border-primary/40" : config.border,
                )}
              >
                <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
                  <div className="min-w-0 flex-1 space-y-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <h2 className="text-lg font-semibold text-foreground">{provider.provider}</h2>
                      <span
                        className={cn(
                          "inline-flex items-center gap-2 rounded-full border px-2.5 py-1 text-xs font-medium",
                          config.pill,
                        )}
                      >
                        <span className={cn("h-2 w-2 rounded-full", config.dot)} />
                        {config.label}
                      </span>
                    </div>

                    <div className="grid gap-2 text-sm md:grid-cols-2">
                      <div>
                        <span className="text-muted-foreground">Model</span>
                        <p className="font-medium text-foreground">{provider.model || "Not set"}</p>
                      </div>
                      <div>
                        <span className="text-muted-foreground">Dimension</span>
                        <p className="font-medium text-foreground">
                          {typeof provider.dimension === "number" ? provider.dimension : "Not set"}
                        </p>
                      </div>
                      <div className="md:col-span-2">
                        <span className={cn("text-muted-foreground", config.text)}>Last checked</span>
                        <p className="font-medium text-foreground">{lastChecked}</p>
                      </div>
                    </div>

                    {provider.message ? (
                      <p className="text-sm text-muted-foreground">{provider.message}</p>
                    ) : null}
                  </div>

                  <div className="flex items-center">
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => handleTest(provider.provider)}
                      disabled={Boolean(testingProvider)}
                    >
                      <RefreshCw className={cn("h-4 w-4", testingProvider === provider.provider && "animate-spin")} />
                      <span>{testingProvider === provider.provider ? "Testing..." : "Test"}</span>
                    </Button>
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      ) : null}
    </motion.div>
  )
}

export default function SettingsPage() {
  return (
    <Suspense fallback={null}>
      <SettingsContent />
    </Suspense>
  )
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <span className="text-muted-foreground">{label}</span>
      <p className="font-medium text-foreground">{value}</p>
    </div>
  )
}

function formatStatus(value: string) {
  return value
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ")
}

function upsertProvider(
  providers: ProviderDiagnostic[],
  provider: ProviderDiagnostic,
): ProviderDiagnostic[] {
  const next = providers.findIndex((item) => item.provider === provider.provider)
  if (next === -1) {
    return [...providers, provider].sort((left, right) => left.provider.localeCompare(right.provider))
  }

  return providers
    .map((item) => (item.provider === provider.provider ? { ...item, ...provider } : item))
    .sort((left, right) => left.provider.localeCompare(right.provider))
}

function formatLastChecked(lastChecked?: string) {
  if (!lastChecked) return "Not checked yet"

  const date = new Date(lastChecked)
  if (Number.isNaN(date.getTime())) return lastChecked

  return date.toLocaleString()
}
