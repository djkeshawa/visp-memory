"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import type { DreamProposal } from "@/lib/dreaming-types"

const labels = {
  duplicate: "Duplicate notes", related: "Related memories", conflict: "Possible contradiction", expired: "Validity period ended",
}

export function DreamProposalCard({ item, busy, onReview, onUndo }: {
  item: DreamProposal
  busy: boolean
  onReview?: (decision: "archive" | "dismiss") => void
  onUndo?: () => void
}) {
  const [copyState, setCopyState] = useState("")
  return <article className="rounded-xl border border-border bg-card p-5">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h3 className="font-medium">{labels[item.kind]}</h3>
      <span className="rounded-full bg-secondary px-2.5 py-1 text-xs text-muted-foreground">
        {item.resolution === "applied" ? "Applied · recoverable" : item.resolution === "undone" ? "Undone"
          : item.resolution === "dismissed" ? "Dismissed" : item.automatic ? "Eligible for automatic merge" : "For review"}
      </span>
    </div>
    <p className="mt-3 text-sm leading-6 text-muted-foreground">{item.reason}</p>
    <details className="mt-3 text-sm">
      <summary className="cursor-pointer font-medium">Inspect {item.sources.length} source {item.sources.length === 1 ? "memory" : "memories"}</summary>
      <ul className="mt-3 space-y-3">
        {item.sources.map((source) => <li key={source.id} className="rounded-lg bg-secondary/60 p-3">
          <p className="whitespace-pre-wrap break-words text-sm">{source.content}</p>
          <p className="mt-2 break-all text-xs text-muted-foreground">Source: {source.id}</p>
        </li>)}
      </ul>
    </details>
    {item.draft_summary && <details className="mt-3 text-sm">
      <summary className="cursor-pointer font-medium">Review draft summary</summary>
      <p className="my-2 text-xs text-muted-foreground">Source excerpts for review. This draft has not been saved as new knowledge.</p>
      <pre className="whitespace-pre-wrap break-words rounded-lg bg-secondary/60 p-3 font-sans text-sm">{item.draft_summary}</pre>
      <Button className="mt-2" size="sm" variant="outline" onClick={async () => {
        try { await navigator.clipboard.writeText(item.draft_summary!); setCopyState("Copied") }
        catch { setCopyState("Select the draft text to copy it") }
      }}>Copy draft</Button>
      <span className="ml-2 text-xs" role="status">{copyState}</span>
    </details>}
    <div className="mt-4 flex flex-wrap gap-2">
      {item.resolution === "pending" && onReview && <>
        {item.kind === "expired" && <Button size="sm" disabled={busy} onClick={() => onReview("archive")}>Archive this memory</Button>}
        <Button size="sm" variant="outline" disabled={busy} onClick={() => onReview("dismiss")}>Dismiss suggestion</Button>
      </>}
      {item.resolution === "applied" && item.action_id && onUndo && <Button size="sm" variant="outline" disabled={busy} onClick={onUndo}>Undo change</Button>}
    </div>
  </article>
}
