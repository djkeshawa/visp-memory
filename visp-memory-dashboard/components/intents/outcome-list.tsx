"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Pill } from "@/components/strata/primitives"
import { IntentEditDialog } from "@/components/intents/intent-edit-dialog"
import type { IntentUpdate } from "@/components/intents/intent-card"
import { WorkflowDetails } from "@/components/intents/workflow-details"
import { shortDate } from "@/lib/memory-trust"
import type { Intent } from "@/lib/types"

function OutcomeRow({ intent, onReopen, onUpdate }: { intent: Intent; onReopen: (intent: Intent) => void; onUpdate: (intent: Intent, updates: IntentUpdate) => void }) {
  const [editing, setEditing] = useState(false)
  const date = shortDate(intent.updatedAt || intent.createdAt)
  return (
    <li className="flex flex-col gap-2 border-t border-border px-4 py-3.5 first:border-t-0 sm:px-5">
      <div className="flex items-start justify-between gap-3">
        <span className="min-w-0 font-medium">{intent.description}</span>
        <Pill tone={intent.status === "completed" ? "success" : "neutral"} className="capitalize">{intent.status}</Pill>
      </div>
      <span className="text-xs text-muted-foreground">{date ? `${date} · ` : ""}<span className="font-mono">{intent.id}</span></span>
      <WorkflowDetails intent={intent} />
      <div className="flex flex-wrap gap-2">
        <Button variant="ghost" size="sm" onClick={() => setEditing(true)} aria-label="Edit intent">Edit</Button>
        <Button variant="outline" size="sm" onClick={() => onReopen(intent)} title="Reopen intent">Reopen</Button>
      </div>
      {editing ? <IntentEditDialog intent={intent} open onOpenChange={setEditing} onSave={(updates) => onUpdate(intent, updates)} /> : null}
    </li>
  )
}

export function OutcomeList({ intents, onReopen, onUpdate }: { intents: Intent[]; onReopen: (intent: Intent) => void; onUpdate: (intent: Intent, updates: IntentUpdate) => void }) {
  return (
    <section aria-labelledby="done-h" className="flex min-w-0 flex-1 basis-80 flex-col gap-3">
      <h2 id="done-h" className="eyebrow">Recorded outcomes · {intents.length}</h2>
      {intents.length === 0 ? (
        <p className="rounded-2xl border border-dashed border-border p-8 text-center text-sm text-muted-foreground">No recorded outcomes</p>
      ) : (
        <ul className="surface overflow-hidden rounded-2xl">
          {intents.map((intent) => <OutcomeRow key={intent.id} intent={intent} onReopen={onReopen} onUpdate={onUpdate} />)}
        </ul>
      )}
      <p className="text-xs text-muted-foreground">Outcomes reported by a workflow are recorded as reported. Memory does not decide whether work is done.</p>
    </section>
  )
}
