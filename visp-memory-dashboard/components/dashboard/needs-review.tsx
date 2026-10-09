"use client"

import { useEffect, useState } from "react"
import Link from "next/link"
import { Panel, StatusDot } from "@/components/strata/primitives"
import { dreamingRequest, describeApiError } from "@/lib/api"
import type { DreamProposal, DreamStatus } from "@/lib/dreaming-types"
import type { TrustTone } from "@/lib/memory-trust"
import { shortDate } from "@/lib/memory-trust"
import { projectHref } from "@/lib/project-selection"

const KIND: Record<DreamProposal["kind"], { title: string; tone: TrustTone; hollow?: boolean }> = {
  conflict: { title: "Possible contradiction", tone: "danger" },
  expired: { title: "Validity period ended", tone: "warning" },
  duplicate: { title: "Duplicate notes", tone: "neutral", hollow: true },
  related: { title: "Related memories", tone: "info" },
}
const SHOWN = 3

/** Pending proposals from the latest dreaming run. Renders nothing without a project; says so when the run cannot be read. */
export function NeedsReview({ repoId }: { repoId: string | null }) {
  const [state, setState] = useState<{ repoId: string; pending: DreamProposal[]; date: string | null } | null>(null)

  const [failure, setFailure] = useState<{ repoId: string; message: string } | null>(null)

  useEffect(() => {
    setState(null)
    setFailure(null)
    if (!repoId) return
    let current = true
    dreamingRequest<DreamStatus>(repoId)
      .then((status) => {
        if (!current) return
        const run = status.runs?.[0]
        const pending = (run?.proposals || []).filter((item) => !item.resolution || item.resolution === "pending")
        setState({ repoId, pending, date: shortDate(run?.created_at) })
      })
      .catch((failure) => { if (current) setFailure({ repoId, message: describeApiError(failure) }) })
    return () => { current = false }
  }, [repoId])

  if (!repoId) return null
  if (failure && failure.repoId === repoId) {
    return (
      <Panel title="Needs your review" titleId="review-heading">
        <p role="status" className="text-sm text-muted-foreground">Could not load this panel: {failure.message}</p>
      </Panel>
    )
  }
  if (!state || state.repoId !== repoId) return null
  const { pending, date } = state
  const href = projectHref("/dreaming", repoId)

  return (
    <Panel title="Needs your review" titleId="review-heading" aside={<span className="text-xs text-muted-foreground">{date ? `from the run on ${date}` : "from the latest dreaming run"}</span>}>
      {pending.length === 0 ? (
        <p className="text-sm text-muted-foreground">Nothing is waiting for review.</p>
      ) : (
        <div className="flex flex-col gap-2.5">
          {pending.slice(0, SHOWN).map((item) => {
            const kind = KIND[item.kind] || KIND.related
            return (
              <Link key={item.id} href={href} className="well flex min-h-12 gap-3 rounded-xl p-3 transition-colors hover:bg-secondary">
                <StatusDot tone={kind.tone} hollow={kind.hollow} className="mt-2" />
                <span className="flex min-w-0 flex-col gap-0.5">
                  <span className="text-sm font-semibold">{kind.title}</span>
                  <span className="line-clamp-2 break-words text-[13px] text-muted-foreground">{item.reason}</span>
                </span>
              </Link>
            )
          })}
          <Link href={href} className="mt-1 text-[13px] font-medium text-highlight hover:underline">
            {pending.length > SHOWN ? `Review all ${pending.length}` : "Open review"}<span aria-hidden="true"> →</span>
          </Link>
        </div>
      )}
    </Panel>
  )
}
