"use client"

import { useState } from "react"
import { Sparkles } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Pill } from "@/components/strata/primitives"
import { Criteria } from "@/components/intents/criteria"
import { IntentEditDialog } from "@/components/intents/intent-edit-dialog"
import { WorkflowDetails } from "@/components/intents/workflow-details"
import type { Intent } from "@/lib/types"

export type IntentUpdate = { description: string; priority: number }
export const PRIORITY_TONE = { high: "warning", medium: "info", low: "neutral" } as const

/** An active intent: priority, acceptance criteria, what the workflow reported, and the advisory outcome actions. */
export function IntentCard({ intent, onComplete, onClose, onUpdate }: {
  intent: Intent
  onComplete: (intent: Intent) => void
  onClose: (intent: Intent) => void
  onUpdate: (intent: Intent, updates: IntentUpdate) => void
}) {
  const [editing, setEditing] = useState(false)
  const suggested = intent.context?.completed_automatically
  return (
    <article aria-labelledby={`intent-${intent.id}`} className="surface flex flex-col gap-3.5 rounded-2xl p-5">
      <div className="flex flex-wrap items-start justify-between gap-2.5">
        <h3 id={`intent-${intent.id}`} className="min-w-0 flex-1 basis-64 text-base font-semibold leading-snug">{intent.description}</h3>
        <Pill tone={PRIORITY_TONE[intent.priority]}>{intent.priority.charAt(0).toUpperCase() + intent.priority.slice(1)} priority</Pill>
      </div>
      {suggested ? (
        <p className="flex items-center gap-1.5 text-xs text-highlight">
          <Sparkles className="h-3 w-3" aria-hidden="true" />
          Completed automatically · {Math.round(Number(intent.context?.completion_evaluation?.confidence || 0) * 100)}%
        </p>
      ) : null}
      <Criteria intent={intent} />
      <WorkflowDetails intent={intent} />
      <div className="flex flex-wrap items-center justify-between gap-2.5 border-t border-border pt-3">
        <span className="truncate font-mono text-xs text-muted-foreground" title={intent.id}>{intent.id}</span>
        <div className="flex flex-wrap gap-2">
          <Button variant="ghost" onClick={() => setEditing(true)} aria-label="Edit intent">Edit</Button>
          <Button variant="outline" onClick={() => onClose(intent)} aria-label="Record close outcome" title="Record close outcome">Record close</Button>
          <Button onClick={() => onComplete(intent)} aria-label="Record completion outcome" title="Record completion outcome">Record completion</Button>
        </div>
      </div>
      {editing ? <IntentEditDialog intent={intent} open onOpenChange={setEditing} onSave={(updates) => onUpdate(intent, updates)} /> : null}
    </article>
  )
}
