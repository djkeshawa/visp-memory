"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Pill, StatusDot } from "@/components/strata/primitives"
import type { TrustTone } from "@/lib/memory-trust"
import type { DreamProposal } from "@/lib/dreaming-types"
import { cn } from "@/lib/utils"

const KINDS: Record<DreamProposal["kind"], { label: string; tone: TrustTone; hollow?: boolean }> = {
  duplicate: { label: "Duplicate notes", tone: "neutral", hollow: true },
  related: { label: "Related memories", tone: "info" },
  conflict: { label: "Possible contradiction", tone: "danger" },
  expired: { label: "Validity period ended", tone: "warning" },
}

function resolutionLabel(item: DreamProposal): string {
  if (item.resolution === "applied") return "Applied · recoverable"
  if (item.resolution === "undone") return "Undone"
  if (item.resolution === "dismissed") return "Dismissed"
  return item.automatic ? "Eligible for automatic merge" : "For review"
}

/** One finding: why it was raised, the source memories side by side, and the actions the server allows. */
export function DreamProposalCard({ item, busy, onReview, onUndo }: {
  item: DreamProposal
  busy: boolean
  onReview?: (decision: "archive" | "dismiss") => void
  onUndo?: () => void
}) {
  const [copyState, setCopyState] = useState("")
  const kind = KINDS[item.kind]
  const titleId = `proposal-${item.id}`
  const letters = item.sources.length > 1
  const copyDraft = async () => {
    try { await navigator.clipboard.writeText(item.draft_summary!); setCopyState("Copied") }
    catch { setCopyState("Select the draft text to copy it") }
  }
  return (
    <article aria-labelledby={titleId} className={cn("surface flex flex-col gap-4 rounded-2xl p-5 sm:p-6", item.kind === "conflict" && "border-destructive/40")}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 id={titleId} className="flex items-center gap-2.5 text-base font-semibold"><StatusDot tone={kind.tone} hollow={kind.hollow} />{kind.label}</h3>
        <Pill>{resolutionLabel(item)}</Pill>
      </div>
      <p className="text-sm leading-6 text-muted-foreground">{item.reason}</p>
      <ul aria-label={`${item.sources.length} source ${item.sources.length === 1 ? "memory" : "memories"}`} className="grid gap-3 [grid-template-columns:repeat(auto-fit,minmax(min(100%,16rem),1fr))]">
        {item.sources.map((source, index) => (
          <li key={source.id} className="well flex flex-col gap-2 rounded-xl p-3.5">
            <span className="break-all font-mono text-xs text-muted-foreground">{letters ? `${String.fromCharCode(65 + index)} · ` : ""}{source.id}</span>
            <span className="whitespace-pre-wrap break-words text-sm leading-6">{source.content}</span>
          </li>
        ))}
      </ul>
      {item.draft_summary ? (
        <div className="flex flex-col gap-2 rounded-xl border border-dashed border-input p-3.5">
          <span className="text-xs font-semibold text-muted-foreground">Draft summary · not saved as new knowledge</span>
          <p className="whitespace-pre-wrap break-words text-sm leading-6">{item.draft_summary}</p>
          <span className="text-xs text-muted-foreground">Source excerpts for review, not a stored fact.</span>
        </div>
      ) : null}
      <div className="flex flex-wrap items-center gap-2">
        {item.resolution === "pending" && onReview ? (
          <>
            {item.kind === "expired" ? <Button disabled={busy} onClick={() => onReview("archive")}>Archive this memory</Button> : null}
            <Button variant="outline" disabled={busy} onClick={() => onReview("dismiss")}>Dismiss suggestion</Button>
          </>
        ) : null}
        {item.draft_summary ? <Button variant="outline" onClick={() => void copyDraft()}>Copy draft</Button> : null}
        {item.resolution === "applied" && item.action_id && onUndo ? <Button variant="outline" disabled={busy} onClick={onUndo}>Undo change</Button> : null}
        {copyState ? <span className="text-xs" role="status">{copyState}</span> : null}
      </div>
    </article>
  )
}
