"use client"

import { useEffect, useState } from "react"
import Link from "next/link"
import { Pill } from "@/components/strata/primitives"
import { MEMORY_LAYER_CONFIG, normalizeMemoryLayer } from "@/lib/layers"
import { shortDate } from "@/lib/memory-trust"
import { projectHref } from "@/lib/project-selection"
import type { SearchResult } from "@/lib/types"
import { cn } from "@/lib/utils"

function Row({ label, children, mono }: { label: string; children: React.ReactNode; mono?: boolean }) {
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className={cn("m-0 min-w-0 break-words", mono && "font-mono text-xs [overflow-wrap:anywhere]")}>{children}</dd>
    </>
  )
}

/** Copy the memory id, which is what a citation refers to. */
function CopyCitation({ id }: { id: string }) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle")
  useEffect(() => setState("idle"), [id])
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(id)
      setState("copied")
    } catch {
      setState("failed")
    }
  }
  return (
    <>
      <button type="button" onClick={() => void copy()} className="h-11 rounded-[10px] border border-border px-3.5 text-sm font-medium transition-colors hover:bg-secondary">Copy citation</button>
      <span role="status" className="sr-only">{state === "copied" ? "Citation copied" : state === "failed" ? "Could not copy the citation" : ""}</span>
    </>
  )
}

export function Inspector({ memory, repoId }: { memory: SearchResult; repoId: string | null }) {
  const flags = memory.qualityFlags || []
  const expired = !!memory.validTo && Date.parse(memory.validTo) < Date.now()
  const conflicts = flags.some((flag) => /contradict|conflict/.test(flag)) || !!memory.metadata?.conflict
  const stale = expired || flags.some((flag) => /stale|decay/.test(flag))
  const external = memory.tags?.includes("provenance:external")
  const method = memory.retrievalMethod === "semantic" ? "Semantic search" : memory.retrievalMethod === "keyword" ? "Keyword search" : null
  const config = MEMORY_LAYER_CONFIG[normalizeMemoryLayer(memory.layer)]
  const evidence = memory.evidenceIds?.length || 0

  return (
    <aside aria-labelledby="inspector-heading" className="surface flex min-w-0 flex-[1_1_340px] flex-col gap-[18px] rounded-2xl p-5 sm:p-[22px]">
      <div className="flex items-center justify-between gap-2">
        <h2 id="inspector-heading" className="eyebrow">Inspector</h2>
        <span className="min-w-0 truncate font-mono text-xs text-muted-foreground" title={memory.id}>{memory.id}</span>
      </div>
      <p className="m-0 whitespace-pre-wrap break-words text-base leading-relaxed">{memory.content}</p>
      <div className="flex flex-wrap gap-1.5">
        <Pill className={cn("max-w-full truncate bg-secondary", config.color)}>{config.label} · {memory.category}</Pill>
        {method && <Pill>{method}</Pill>}
        {external && <Pill tone="neutral">Excluded from task briefs</Pill>}
        {stale && <Pill tone="warning">May be stale · review before using</Pill>}
        {conflicts && <Pill tone="danger">Possible conflict · review sources</Pill>}
        {memory.epistemicStatus === "unknown" && <Pill>Unverified knowledge</Pill>}
      </div>

      <div className="well flex flex-col gap-1.5 rounded-[10px] p-3.5">
        <span className="text-xs font-semibold text-muted-foreground">Why it matched</span>
        <span className="break-words text-sm">{memory.matchExplanation || "The server did not report why this matched."}</span>
        {!method && memory.similarity !== undefined && <span className="text-xs text-muted-foreground">The server did not report the search method.</span>}
        {memory.relevanceScore !== undefined && (
          <span className="text-xs text-muted-foreground">Ranking score {memory.relevanceScore.toFixed(2)} orders results. It is not a probability of correctness.</span>
        )}
      </div>

      <dl className="m-0 grid grid-cols-[max-content_minmax(0,1fr)] gap-x-4 gap-y-2.5 text-[13px]">
        <Row label="Source">{memory.source || "Not recorded"}</Row>
        <Row label="Evidence">{evidence} linked evidence records</Row>
        <Row label="Reviewed">{memory.approvedAt ? `Reviewed ${shortDate(memory.approvedAt) ?? memory.approvedAt}` : "No approval recorded"}</Row>
        <Row label="Valid until">{memory.validTo ? shortDate(memory.validTo) ?? memory.validTo : "No end date"}</Row>
        <Row label="Files" mono>{memory.files?.length ? memory.files.join(", ") : "—"}</Row>
        {!!flags.length && <Row label="Quality flags">{flags.map((flag) => flag.replaceAll("_", " ")).join(", ")}</Row>}
      </dl>

      {external && (
        <p className="m-0 text-xs leading-5 text-muted-foreground">
          Dashboard and API notes remain searchable, but their external provenance excludes them from task briefs and automatic context.
          Verify the original source, then use the local CLI to record your own reviewed conclusion in the same project and store, including a source reference.
          Marking a note active does not change its provenance.
        </p>
      )}

      <div className="flex flex-wrap items-center gap-2 pt-1">
        <CopyCitation id={memory.id} />
        {external ? null : <Link href={projectHref("/brief", repoId)} className="inline-flex h-11 items-center rounded-[10px] border border-border px-4 text-sm font-medium transition-colors hover:bg-secondary">Open task brief</Link>}
        <Link href={projectHref("/memories", repoId)} className="inline-flex h-11 items-center px-1.5 text-sm font-medium text-highlight hover:underline">Open in library</Link>
      </div>
    </aside>
  )
}
