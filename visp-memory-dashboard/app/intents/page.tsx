"use client"

import { Suspense, useEffect, useRef, useState } from "react"
import type { FormEvent } from "react"
import { AlertTriangle } from "lucide-react"
import { PageHeader } from "@/components/strata/primitives"
import { IntentCard, type IntentUpdate } from "@/components/intents/intent-card"
import { IntentForm } from "@/components/intents/intent-form"
import { OutcomeList } from "@/components/intents/outcome-list"
import { closeIntent, completeIntent, createIntent, describeApiError, getIntents, reopenIntent, updateIntent } from "@/lib/api"
import { useSelectedProjectId } from "@/lib/project-selection"
import type { Intent, IntentOutcomeResponse } from "@/lib/types"

type Outcome = "Completion" | "Close" | "Reopen"

function IntentsContent() {
  const selectedRepoId = useSelectedProjectId()
  const [intents, setIntents] = useState<Intent[]>([])
  const [newDescription, setNewDescription] = useState("")
  const [newPriority, setNewPriority] = useState(5)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [outcomeMessage, setOutcomeMessage] = useState<string | null>(null)
  const selectedRepoIdRef = useRef(selectedRepoId)
  const requestGenerationRef = useRef(0)
  selectedRepoIdRef.current = selectedRepoId

  useEffect(() => {
    ++requestGenerationRef.current
    setIntents([])
    setErrorMessage(null)
    setOutcomeMessage(null)
    void fetchIntents()
    const refresh = () => { if (document.visibilityState === "visible") void fetchIntents() }
    const timer = window.setInterval(refresh, 15000)
    window.addEventListener("focus", refresh)
    return () => { window.clearInterval(timer); window.removeEventListener("focus", refresh) }
  }, [selectedRepoId])

  const fetchIntents = () => {
    const requestedRepoId = selectedRepoId
    if (selectedRepoIdRef.current !== requestedRepoId) return
    const generation = ++requestGenerationRef.current
    getIntents(requestedRepoId, "all")
      .then((data) => {
        if (generation !== requestGenerationRef.current || selectedRepoIdRef.current !== requestedRepoId) return
        setIntents(data)
        setErrorMessage(null)
      })
      .catch((error) => {
        if (generation !== requestGenerationRef.current || selectedRepoIdRef.current !== requestedRepoId) return
        console.error(error)
        setErrorMessage(describeApiError(error))
      })
  }

  // Every mutation is dropped if the selected project changed while it was in flight.
  const mutate = async (work: () => Promise<void>, failure: string) => {
    const requestedRepoId = selectedRepoId
    try {
      await work()
      if (selectedRepoIdRef.current !== requestedRepoId) return false
      return true
    } catch (error) {
      if (selectedRepoIdRef.current !== requestedRepoId) return false
      console.error(failure, error)
      setErrorMessage(describeApiError(error))
      return false
    }
  }

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault()
    if (!newDescription.trim()) return
    setIsSubmitting(true)
    const ok = await mutate(async () => { await createIntent(newDescription, newPriority, selectedRepoId) }, "Failed to create intent")
    setIsSubmitting(false)
    if (!ok) return
    setErrorMessage(null)
    setNewDescription("")
    setNewPriority(5)
    fetchIntents()
  }

  const recordOutcome = (name: Outcome, send: (id: string) => Promise<IntentOutcomeResponse>) => async (intent: Intent) => {
    let outcome: IntentOutcomeResponse | undefined
    const ok = await mutate(async () => { outcome = await send(intent.id) }, `Failed to record ${name.toLowerCase()} outcome`)
    if (!ok || !outcome) return
    setOutcomeMessage(formatOutcomeMessage(name, outcome.statusChanged, outcome.status))
    fetchIntents()
  }
  const handleComplete = recordOutcome("Completion", completeIntent)
  const handleClose = recordOutcome("Close", closeIntent)
  const handleReopen = recordOutcome("Reopen", reopenIntent)

  const handleUpdate = async (intent: Intent, updates: IntentUpdate) => {
    const ok = await mutate(async () => { await updateIntent(intent.id, updates) }, "Failed to update intent")
    if (ok) fetchIntents()
  }

  const activeIntents = intents.filter((item) => item.status === "active")
  const closedIntents = intents.filter((item) => item.status === "completed" || item.status === "closed")

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <PageHeader eyebrow="Goals your assistant or workflow reports against" title="Intents" description="Track goals and follow status reported by your assistant or workflow." />
      <IntentForm description={newDescription} priority={newPriority} submitting={isSubmitting} onDescription={setNewDescription} onPriority={setNewPriority} onSubmit={handleSubmit} />

      {errorMessage ? (
        <div role="alert" className="flex items-start gap-2 rounded-xl border border-destructive/30 bg-destructive/5 p-4 text-sm">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" aria-hidden="true" />
          <span>{errorMessage}</span>
        </div>
      ) : null}
      {outcomeMessage ? <div role="status" className="rounded-xl border border-highlight/30 bg-accent p-4 text-sm text-accent-foreground">{outcomeMessage}</div> : null}

      <div className="flex flex-wrap items-start gap-6">
        <section aria-labelledby="active-h" className="flex min-w-0 flex-[999_1_32rem] flex-col gap-3">
          <h2 id="active-h" className="eyebrow">Active · {activeIntents.length}</h2>
          {activeIntents.length === 0 ? (
            <p className="rounded-2xl border border-dashed border-border p-8 text-center text-sm text-muted-foreground">No active intents</p>
          ) : activeIntents.map((intent) => (
            <IntentCard key={intent.id} intent={intent} onComplete={handleComplete} onClose={handleClose} onUpdate={handleUpdate} />
          ))}
        </section>
        <OutcomeList intents={closedIntents} onReopen={handleReopen} onUpdate={handleUpdate} />
      </div>
    </div>
  )
}

export default function IntentsPage() {
  return <Suspense fallback={null}><IntentsContent /></Suspense>
}

function formatOutcomeMessage(outcomeName: Outcome, statusChanged: boolean, status: Intent["status"]): string {
  if (statusChanged) {
    return `${outcomeName} outcome recorded and the backend changed the authoritative status to ${status}.`
  }
  return `${outcomeName} outcome recorded. The authoritative intent remains ${status} until its workflow authority changes it.`
}
