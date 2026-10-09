import { Pill } from "@/components/strata/primitives"
import type { DecayPreviewItem, DuplicateCandidate, GraphLink, MemoryIntelligenceReportItem, RelationshipEvidence } from "@/lib/types"
import { ITEM_LIMIT, ItemRow } from "./section-card"

const formatKey = (value: string) => value.replaceAll("_", " ")

function formatFact(value: unknown): string {
  if (value === null || value === undefined) return "-"
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : value.toFixed(2)
  if (typeof value === "string" || typeof value === "boolean") return String(value)
  return JSON.stringify(value)
}

function FactList({ facts }: { facts: Record<string, unknown> }) {
  const entries = Object.entries(facts).slice(0, 4)
  if (!entries.length) return null
  return (
    <dl className="mt-1.5 grid gap-x-4 gap-y-1.5 text-xs text-muted-foreground sm:grid-cols-2">
      {entries.map(([key, value]) => (
        <div key={key} className="min-w-0">
          <dt className="font-medium text-foreground">{formatKey(key)}</dt>
          <dd className="truncate">{formatFact(value)}</dd>
        </div>
      ))}
    </dl>
  )
}

export function ReportItems({ items }: { items: MemoryIntelligenceReportItem[] }) {
  return items.slice(0, ITEM_LIMIT).map((item, index) => (
    <ItemRow
      key={`${item.type}:${item.id}:${index}`}
      title={item.title}
      reason={item.reason}
      meta={<><span>{item.type}</span><span className="max-w-full truncate font-mono">{item.id}</span></>}
    >
      <FactList facts={item.facts} />
    </ItemRow>
  ))
}

const CONFIDENCE_TONE = { observed: "success", inferred: "neutral", ambiguous: "warning", manual: "info" } as const

function EvidenceSummary({ evidence }: { evidence?: RelationshipEvidence | null }) {
  if (!evidence) return <span className="text-[13px] text-muted-foreground">No evidence metadata.</span>
  const source = evidence.source || [evidence.source_file, evidence.source_location].filter(Boolean).join(":")
  return (
    <span className="flex flex-col gap-1">
      <span className="flex flex-wrap items-center gap-2 text-[13px] text-muted-foreground">
        <Pill tone={CONFIDENCE_TONE[evidence.confidence ?? "ambiguous"]}>{evidence.confidence || "ambiguous"}</Pill>
        {typeof evidence.confidence_score === "number" ? <span>score {evidence.confidence_score.toFixed(2)}</span> : null}
      </span>
      {evidence.reason ? <span className="text-[13px] text-muted-foreground">{evidence.reason}</span> : null}
      {source ? <span className="break-words font-mono text-xs text-muted-foreground">{source}</span> : null}
    </span>
  )
}

export function GraphEvidenceItems({ links }: { links: GraphLink[] }) {
  return links.slice(0, ITEM_LIMIT).map((link, index) => (
    <ItemRow
      key={`${link.source}:${link.target}:${link.label}:${index}`}
      title={link.label || "related"}
      meta={<span className="max-w-full break-all font-mono">{link.source} to {link.target}</span>}
    >
      <EvidenceSummary evidence={link.evidence} />
    </ItemRow>
  ))
}

export function DuplicateItems({ candidates }: { candidates: DuplicateCandidate[] }) {
  return candidates.slice(0, ITEM_LIMIT).map((candidate, index) => (
    <ItemRow
      key={`${candidate.ids.join(":")}:${index}`}
      title={candidate.contents[0] || "Duplicate memory"}
      reason={candidate.reason}
      meta={<><Pill>{candidate.ids.length} matches</Pill><span>{candidate.layer}</span><span>similarity {candidate.similarity.toFixed(2)}</span></>}
    />
  ))
}

const RISK_TONE = { likely_to_decay: "danger", weakening: "warning", at_floor: "neutral", stable: "neutral" } as const

export function FreshnessItems({ candidates }: { candidates: DecayPreviewItem[] }) {
  return candidates.slice(0, ITEM_LIMIT).map((candidate) => (
    <ItemRow
      key={candidate.memoryId}
      title={candidate.snippet}
      reason={candidate.reason}
      meta={<><Pill tone={RISK_TONE[candidate.risk]}>{formatKey(candidate.risk)}</Pill><span>idle {Math.round(candidate.ageDays)}d</span></>}
    />
  ))
}
