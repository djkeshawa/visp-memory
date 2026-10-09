"use client"

import { useEffect, useState } from "react"
import Link from "next/link"
import { Panel } from "@/components/strata/primitives"
import { getIntents, describeApiError } from "@/lib/api"
import { projectHref } from "@/lib/project-selection"
import type { Intent } from "@/lib/types"
import { cn } from "@/lib/utils"

const PRIORITY_LABEL: Record<Intent["priority"], string> = { high: "High", medium: "Medium", low: "Low" }
const SHOWN = 4

/** Active intents for the project. Renders nothing without a project; says so when they cannot be read. */
export function ActiveIntents({ repoId }: { repoId: string | null }) {
  const [state, setState] = useState<{ repoId: string; intents: Intent[] } | null>(null)

  const [failure, setFailure] = useState<{ repoId: string; message: string } | null>(null)

  useEffect(() => {
    setState(null)
    setFailure(null)
    if (!repoId) return
    let current = true
    getIntents(repoId, "active")
      .then((intents) => { if (current) setState({ repoId, intents }) })
      .catch((failure) => { if (current) setFailure({ repoId, message: describeApiError(failure) }) })
    return () => { current = false }
  }, [repoId])

  if (!repoId) return null
  if (failure && failure.repoId === repoId) {
    return (
      <Panel title="Active intents" titleId="intents-heading">
        <p role="status" className="text-sm text-muted-foreground">Could not load this panel: {failure.message}</p>
      </Panel>
    )
  }
  if (!state || state.repoId !== repoId) return null
  const intents = [...state.intents].sort((a, b) => (b.priorityValue ?? 0) - (a.priorityValue ?? 0))

  return (
    <Panel title="Active intents" titleId="intents-heading">
      {intents.length === 0 ? (
        <p className="text-sm text-muted-foreground">No active intents.</p>
      ) : (
        <ul className="flex flex-col gap-2.5">
          {intents.slice(0, SHOWN).map((intent) => (
            <li key={intent.id} className="flex justify-between gap-3 text-sm">
              <span className="min-w-0 break-words">{intent.description}</span>
              <span className={cn("shrink-0 text-xs", intent.priority === "high" ? "text-intent" : "text-muted-foreground")}>{PRIORITY_LABEL[intent.priority]}</span>
            </li>
          ))}
        </ul>
      )}
      <Link href={projectHref("/intents", repoId)} className="mt-4 inline-block text-[13px] font-medium text-highlight hover:underline">
        {intents.length > SHOWN ? `All ${intents.length} intents` : "Open intents"}<span aria-hidden="true"> →</span>
      </Link>
    </Panel>
  )
}
