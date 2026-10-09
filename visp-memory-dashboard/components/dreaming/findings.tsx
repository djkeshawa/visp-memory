"use client"

import { useEffect, useState } from "react"
import { DreamProposalCard } from "@/components/dreaming/proposal"
import { cn } from "@/lib/utils"
import type { DreamProposal, DreamRun } from "@/lib/dreaming-types"

type Group = "review" | "applied" | "dismissed"

const GROUPS: { id: Group; label: string; empty: string }[] = [
  { id: "review", label: "For review", empty: "Nothing waiting for review in this cycle." },
  { id: "applied", label: "Applied", empty: "Nothing was applied in this cycle." },
  { id: "dismissed", label: "Dismissed", empty: "Nothing was dismissed in this cycle." },
]

/** Pending (and previewed) findings are for review; undone changes stay beside the applied ones they reverse. */
export function groupOf(item: DreamProposal): Group {
  if (item.resolution === "applied" || item.resolution === "undone") return "applied"
  if (item.resolution === "dismissed") return "dismissed"
  return "review"
}

export function Findings({ run, preview, busy, onReview, onUndo }: {
  run: DreamRun
  preview: boolean
  busy: boolean
  onReview: (run: DreamRun, item: DreamProposal, decision: "archive" | "dismiss") => void
  onUndo: (item: DreamProposal) => void
}) {
  const [chosen, setChosen] = useState<Group | null>(null)
  const runKey = preview ? "preview" : run.id
  useEffect(() => setChosen(null), [runKey])

  const counts = Object.fromEntries(GROUPS.map((group) => [group.id, run.proposals.filter((item) => groupOf(item) === group.id).length])) as Record<Group, number>
  const active = chosen ?? (GROUPS.find((group) => counts[group.id] > 0)?.id ?? "review")
  const shown = run.proposals.filter((item) => groupOf(item) === active)
  const automatic = run.proposals.filter((item) => item.automatic).length

  return (
    <section className="flex flex-col gap-4" aria-label="Dreaming findings">
      <div>
        <h2 className="text-lg font-semibold">{preview ? "Cycle preview" : "Cycle findings"}</h2>
        <p className="mt-1 text-sm text-muted-foreground">{run.scanned} memories examined · {automatic} eligible duplicate groups · {run.proposals.length} findings</p>
        {run.partial || run.proposal_limit_reached || run.pair_limit_reached ? (
          <p className="mt-2 text-sm text-muted-foreground">This cycle covers a limited batch. Later cycles rotate through the project; matches across separate batches may need manual review.</p>
        ) : null}
      </div>
      {run.proposals.length === 0 ? (
        <p className="rounded-2xl border border-dashed border-border p-8 text-center text-sm text-muted-foreground">No cleanup or review suggestions in this batch.</p>
      ) : (
        <>
          <div role="group" aria-label="Proposal status" className="flex flex-wrap gap-1.5">
            {GROUPS.map((group) => (
              <button key={group.id} type="button" aria-pressed={active === group.id} onClick={() => setChosen(group.id)}
                className={cn("h-10 rounded-full border px-4 text-[13px]", active === group.id ? "border-primary bg-primary font-semibold text-primary-foreground" : "border-input text-muted-foreground hover:bg-accent hover:text-accent-foreground")}>
                {group.label} · {counts[group.id]}
              </button>
            ))}
          </div>
          {shown.length === 0 ? (
            <p className="rounded-2xl border border-dashed border-border p-8 text-center text-sm text-muted-foreground">{GROUPS.find((group) => group.id === active)?.empty}</p>
          ) : shown.map((item) => (
            <DreamProposalCard key={`${run.id || "preview"}-${item.id}`} item={item} busy={busy}
              onReview={run.id ? (decision) => onReview(run, item, decision) : undefined}
              onUndo={item.action_id ? () => onUndo(item) : undefined} />
          ))}
        </>
      )}
    </section>
  )
}
