"use client"

import { Suspense, useCallback, useEffect, useMemo, useState } from "react"
import type React from "react"
import { motion } from "framer-motion"
import {
  AlertTriangle,
  CircleHelp,
  ClipboardList,
  Copy,
  GitBranch,
  Network,
  RefreshCw,
  ShieldAlert,
  Timer,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  describeApiError,
  getDecayPreview,
  getDuplicateCandidates,
  getGraphData,
  getMemoryIntelligenceReport,
} from "@/lib/api"
import { pageTransition } from "@/lib/animations"
import { useSelectedProjectId } from "@/lib/project-selection"
import type {
  DecayPreviewItem,
  DecayPreviewResponse,
  DuplicateCandidate,
  GraphLink,
  MemoryIntelligenceReport,
  MemoryIntelligenceReportItem,
  RelationshipEvidence,
} from "@/lib/types"
import { cn } from "@/lib/utils"

const FINDING_LIST_LIMIT = 6
const REPORT_SECTION_EXCLUSIONS = new Set(["suggested_questions", "stale_intents", "contradiction_candidates"])

type DisplayFinding = MemoryIntelligenceReportItem & {
  sectionKey?: string
  sectionTitle?: string
}

function IntelligenceContent() {
  const selectedRepoId = useSelectedProjectId()
  const [report, setReport] = useState<MemoryIntelligenceReport | null>(null)
  const [graphEvidence, setGraphEvidence] = useState<GraphLink[]>([])
  const [duplicates, setDuplicates] = useState<DuplicateCandidate[]>([])
  const [freshness, setFreshness] = useState<DecayPreviewResponse | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  const loadIntelligence = useCallback(async () => {
    setIsLoading(true)
    try {
      const [reportData, graphData, duplicateData, freshnessData] = await Promise.all([
        getMemoryIntelligenceReport(selectedRepoId, 8),
        getGraphData(selectedRepoId),
        getDuplicateCandidates({ repoId: selectedRepoId, limit: 8 }),
        getDecayPreview({ repoId: selectedRepoId, limit: 8, halflifeDays: 30, minImportance: 0.1 }),
      ])

      setReport(reportData)
      setGraphEvidence(graphData.links.filter(hasRelationshipEvidence))
      setDuplicates(duplicateData.candidates)
      setFreshness(freshnessData)
      setErrorMessage(null)
    } catch (error) {
      console.error("Failed to load memory intelligence", error)
      setErrorMessage(describeApiError(error))
    } finally {
      setIsLoading(false)
    }
  }, [selectedRepoId])

  useEffect(() => {
    void loadIntelligence()
  }, [loadIntelligence])

  const reportFindings = useMemo(() => {
    if (!report) return []
    return Object.entries(report.sections)
      .filter(([key]) => !REPORT_SECTION_EXCLUSIONS.has(key))
      .flatMap(([key, section]) =>
        section.items.map((item) => ({
          ...item,
          sectionKey: key,
          sectionTitle: section.title,
        })),
      )
  }, [report])

  const staleIntents = report?.sections.stale_intents?.items ?? []
  const conflictCandidates = report?.sections.contradiction_candidates?.items ?? []
  const suggestedQuestions = report?.sections.suggested_questions?.items ?? []
  const freshnessCandidates = (freshness?.candidates ?? []).filter((item) => item.risk !== "stable")
  const qualityFindingCount =
    reportFindings.length +
    staleIntents.length +
    conflictCandidates.length +
    suggestedQuestions.length +
    duplicates.length +
    freshnessCandidates.length

  return (
    <motion.div initial="initial" animate="animate" variants={pageTransition} className="space-y-8">
      <div className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
        <div>
          <h1 className="flex items-center gap-3 text-2xl font-semibold text-foreground">
            <ClipboardList className="h-6 w-6 text-muted-foreground" />
            Memory Intelligence
          </h1>
          <p className="mt-1 text-muted-foreground">
            {selectedRepoId ? `Project: ${selectedRepoId}` : "Report, evidence, and quality signals"}
          </p>
          {report?.asOf ? <p className="mt-1 text-xs text-muted-foreground">As of {formatDate(report.asOf)}</p> : null}
        </div>
        <Button onClick={loadIntelligence} disabled={isLoading} className="h-9 w-fit">
          <RefreshCw className={cn("mr-2 h-4 w-4", isLoading && "animate-spin")} />
          Refresh
        </Button>
      </div>

      {errorMessage ? (
        <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-4 text-sm text-muted-foreground">
          <div className="flex items-start gap-2">
            <AlertTriangle className="mt-0.5 h-4 w-4 text-destructive" />
            <span>{errorMessage}</span>
          </div>
        </div>
      ) : null}

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Metric
          title="Memories"
          value={report?.summary.totalMemories ?? "-"}
          icon={<ClipboardList className="h-4 w-4" />}
        />
        <Metric
          title="Relationships"
          value={report?.summary.totalRelationships ?? "-"}
          icon={<Network className="h-4 w-4" />}
        />
        <Metric
          title="Evidence Links"
          value={graphEvidence.length}
          icon={<GitBranch className="h-4 w-4" />}
        />
        <Metric
          title="Quality Findings"
          value={qualityFindingCount}
          icon={<ShieldAlert className="h-4 w-4" />}
        />
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <Panel title="Key Findings" icon={<ClipboardList className="h-4 w-4" />}>
          <FindingList
            items={reportFindings}
            isLoading={isLoading}
            emptyLabel="No report findings returned."
          />
        </Panel>

        <Panel title="Graph Evidence" icon={<Network className="h-4 w-4" />}>
          <GraphEvidenceList links={graphEvidence} isLoading={isLoading} />
        </Panel>

        <Panel title="Stale Intents" icon={<Timer className="h-4 w-4" />}>
          <FindingList
            items={staleIntents}
            isLoading={isLoading}
            emptyLabel="No stale intents returned."
          />
        </Panel>

        <Panel title="Duplicates" icon={<Copy className="h-4 w-4" />}>
          <DuplicateList candidates={duplicates} isLoading={isLoading} />
        </Panel>

        <Panel title="Conflicts" icon={<AlertTriangle className="h-4 w-4" />}>
          <FindingList
            items={conflictCandidates}
            isLoading={isLoading}
            emptyLabel="No conflict candidates returned."
          />
        </Panel>

        <Panel title="Suggested Questions" icon={<CircleHelp className="h-4 w-4" />}>
          <FindingList
            items={suggestedQuestions}
            isLoading={isLoading}
            emptyLabel="No suggested questions returned."
            itemLabel="questions"
          />
        </Panel>

        <Panel title="Freshness" icon={<RefreshCw className="h-4 w-4" />}>
          <FreshnessList candidates={freshnessCandidates} isLoading={isLoading} />
        </Panel>
      </div>
    </motion.div>
  )
}

function Metric({ title, value, icon }: { title: string; value: string | number; icon: React.ReactNode }) {
  return (
    <div className="glass rounded-xl p-5">
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">{title}</p>
        <div className="rounded-lg bg-secondary p-2 text-muted-foreground">{icon}</div>
      </div>
      <p className="mt-3 text-2xl font-semibold text-foreground">{value}</p>
    </div>
  )
}

function Panel({ title, icon, children }: { title: string; icon: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="glass overflow-hidden rounded-xl">
      <div className="flex items-center gap-2 border-b border-border p-4">
        <div className="rounded-lg bg-secondary p-2 text-muted-foreground">{icon}</div>
        <h2 className="text-base font-semibold text-foreground">{title}</h2>
      </div>
      {children}
    </section>
  )
}

function FindingList({
  items,
  isLoading,
  emptyLabel,
  itemLabel = "findings",
}: {
  items: DisplayFinding[]
  isLoading: boolean
  emptyLabel: string
  itemLabel?: string
}) {
  if (isLoading) return <LoadingRows />
  if (!items.length) return <EmptyState label={emptyLabel} />

  const visibleItems = items.slice(0, FINDING_LIST_LIMIT)

  return (
    <div className="divide-y divide-border">
      {visibleItems.map((item) => (
        <div key={`${item.sectionKey ?? "section"}:${item.type}:${item.id}`} className="p-4">
          <div className="flex flex-wrap items-center gap-2">
            {item.sectionTitle ? (
              <span className="rounded-full bg-primary/10 px-2.5 py-0.5 text-xs font-medium text-primary">
                {item.sectionTitle}
              </span>
            ) : null}
            <span className="rounded-full bg-secondary px-2.5 py-0.5 text-xs font-medium text-muted-foreground">
              {item.type}
            </span>
            <span className="text-xs text-muted-foreground">{item.id}</span>
          </div>
          <p className="mt-2 line-clamp-2 text-sm font-medium text-foreground">{item.title}</p>
          {item.reason ? <p className="mt-1 text-sm text-muted-foreground">{item.reason}</p> : null}
          <FactList facts={item.facts} />
        </div>
      ))}
      <ListCount visible={visibleItems.length} total={items.length} label={itemLabel} />
    </div>
  )
}

function GraphEvidenceList({ links, isLoading }: { links: GraphLink[]; isLoading: boolean }) {
  if (isLoading) return <LoadingRows />
  if (!links.length) return <EmptyState label="No graph evidence returned." />

  const visibleLinks = links.slice(0, FINDING_LIST_LIMIT)

  return (
    <div className="divide-y divide-border">
      {visibleLinks.map((link) => (
        <div key={`${link.source}:${link.target}:${link.label}`} className="p-4">
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded-full bg-secondary px-2.5 py-0.5 text-xs font-medium text-muted-foreground">
              {link.label || "related"}
            </span>
            <span className="text-xs text-muted-foreground">
              {link.source} to {link.target}
            </span>
          </div>
          <EvidenceSummary evidence={link.evidence} />
        </div>
      ))}
      <ListCount visible={visibleLinks.length} total={links.length} label="links" />
    </div>
  )
}

function DuplicateList({ candidates, isLoading }: { candidates: DuplicateCandidate[]; isLoading: boolean }) {
  if (isLoading) return <LoadingRows />
  if (!candidates.length) return <EmptyState label="No duplicate candidates returned." />

  const visibleCandidates = candidates.slice(0, FINDING_LIST_LIMIT)

  return (
    <div className="divide-y divide-border">
      {visibleCandidates.map((candidate) => (
        <div key={candidate.ids.join(":")} className="p-4">
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded-full bg-secondary px-2.5 py-0.5 text-xs font-medium text-muted-foreground">
              {candidate.ids.length} matches
            </span>
            <span className="text-xs text-muted-foreground">{candidate.layer}</span>
          </div>
          <p className="mt-2 line-clamp-2 text-sm font-medium text-foreground">
            {candidate.contents[0] || "Duplicate memory"}
          </p>
          <p className="mt-1 text-sm text-muted-foreground">{candidate.reason}</p>
        </div>
      ))}
      <ListCount visible={visibleCandidates.length} total={candidates.length} label="candidates" />
    </div>
  )
}

function FreshnessList({ candidates, isLoading }: { candidates: DecayPreviewItem[]; isLoading: boolean }) {
  if (isLoading) return <LoadingRows />
  if (!candidates.length) return <EmptyState label="No stale freshness candidates returned." />

  const visibleCandidates = candidates.slice(0, FINDING_LIST_LIMIT)

  return (
    <div className="divide-y divide-border">
      {visibleCandidates.map((candidate) => (
        <div key={candidate.memoryId} className="p-4">
          <div className="flex flex-wrap items-center gap-2">
            <span className={cn("rounded-full px-2.5 py-0.5 text-xs font-medium", riskClass(candidate.risk))}>
              {candidate.risk.replaceAll("_", " ")}
            </span>
            <span className="text-xs text-muted-foreground">idle {Math.round(candidate.ageDays)}d</span>
          </div>
          <p className="mt-2 line-clamp-2 text-sm font-medium text-foreground">{candidate.snippet}</p>
          <p className="mt-1 text-sm text-muted-foreground">{candidate.reason}</p>
        </div>
      ))}
      <ListCount visible={visibleCandidates.length} total={candidates.length} label="memories" />
    </div>
  )
}

function ListCount({ visible, total, label }: { visible: number; total: number; label: string }) {
  if (total <= visible) return null

  return (
    <div className="border-t border-border px-4 py-3 text-xs text-muted-foreground">
      Showing {visible} of {total} {label}.
    </div>
  )
}

function EvidenceSummary({ evidence }: { evidence?: RelationshipEvidence | null }) {
  if (!evidence) return <p className="mt-2 text-sm text-muted-foreground">No evidence metadata.</p>

  return (
    <div className="mt-2 space-y-1 text-sm text-muted-foreground">
      <p>
        {evidence.confidence || "ambiguous"} confidence
        {typeof evidence.confidence_score === "number" ? `, ${Math.round(evidence.confidence_score * 100)}%` : ""}
      </p>
      {evidence.reason ? <p>{evidence.reason}</p> : null}
      {evidence.source ? <p className="text-xs">Source: {evidence.source}</p> : null}
    </div>
  )
}

function FactList({ facts }: { facts: Record<string, unknown> }) {
  const entries = Object.entries(facts).slice(0, 4)
  if (!entries.length) return null

  return (
    <dl className="mt-3 grid gap-2 text-xs text-muted-foreground sm:grid-cols-2">
      {entries.map(([key, value]) => (
        <div key={key} className="min-w-0">
          <dt className="font-medium text-foreground">{formatKey(key)}</dt>
          <dd className="truncate">{formatFact(value)}</dd>
        </div>
      ))}
    </dl>
  )
}

function LoadingRows() {
  return (
    <div className="space-y-3 p-4">
      <div className="h-4 w-28 rounded bg-muted shimmer" />
      <div className="h-4 w-full rounded bg-muted shimmer" />
      <div className="h-4 w-2/3 rounded bg-muted shimmer" />
    </div>
  )
}

function EmptyState({ label }: { label: string }) {
  return <div className="p-5 text-sm text-muted-foreground">{label}</div>
}

function hasRelationshipEvidence(link: GraphLink): boolean {
  const evidence = link.evidence
  return Boolean(evidence?.reason || evidence?.confidence || evidence?.source)
}

function formatDate(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString()
}

function formatKey(value: string): string {
  return value.replaceAll("_", " ")
}

function formatFact(value: unknown): string {
  if (value === null || value === undefined) return "-"
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value)
  }
  return JSON.stringify(value)
}

function riskClass(risk: DecayPreviewItem["risk"]): string {
  if (risk === "likely_to_decay") return "bg-red-50 text-red-700 dark:bg-red-950/40 dark:text-red-300"
  if (risk === "weakening") return "bg-amber-50 text-amber-700 dark:bg-amber-950/40 dark:text-amber-300"
  return "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300"
}

export default function IntelligencePage() {
  return (
    <Suspense fallback={null}>
      <IntelligenceContent />
    </Suspense>
  )
}
