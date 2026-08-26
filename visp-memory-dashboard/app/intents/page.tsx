"use client"

import { Suspense, useEffect, useRef, useState } from "react"
import { motion } from "framer-motion"
import { AlertTriangle, Plus } from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Slider } from "@/components/ui/slider"
import { IntentsColumn } from "@/components/intents/intents-column"
import { closeIntent, completeIntent, createIntent, describeApiError, getIntents, reopenIntent, updateIntent } from "@/lib/api"
import { pageTransition } from "@/lib/animations"
import { useSelectedProjectId } from "@/lib/project-selection"
import type { Intent } from "@/lib/types"

function IntentsContent() {
  const selectedRepoId = useSelectedProjectId()
  const [intents, setIntents] = useState<Intent[]>([])
  const [isDialogOpen, setIsDialogOpen] = useState(false)
  const [newDescription, setNewDescription] = useState("")
  const [newPriority, setNewPriority] = useState([5])
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

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!newDescription.trim()) return

    setIsSubmitting(true)
    const requestedRepoId = selectedRepoId
    try {
      await createIntent(newDescription, newPriority[0], requestedRepoId)
      if (selectedRepoIdRef.current !== requestedRepoId) return
      setErrorMessage(null)
      setNewDescription("")
      setNewPriority([5])
      setIsDialogOpen(false)
      fetchIntents()
    } catch (error) {
      if (selectedRepoIdRef.current !== requestedRepoId) return
      console.error("Failed to create intent", error)
      setErrorMessage(describeApiError(error))
    } finally {
      setIsSubmitting(false)
    }
  }

  const handleComplete = async (intent: Intent) => {
    const requestedRepoId = selectedRepoId
    try {
      const outcome = await completeIntent(intent.id)
      if (selectedRepoIdRef.current !== requestedRepoId) return
      setOutcomeMessage(formatOutcomeMessage("Completion", outcome.statusChanged, outcome.status))
      fetchIntents()
    } catch (error) {
      if (selectedRepoIdRef.current !== requestedRepoId) return
      console.error("Failed to complete intent", error)
      setErrorMessage(describeApiError(error))
    }
  }

  const handleClose = async (intent: Intent) => {
    const requestedRepoId = selectedRepoId
    try {
      const outcome = await closeIntent(intent.id)
      if (selectedRepoIdRef.current !== requestedRepoId) return
      setOutcomeMessage(formatOutcomeMessage("Close", outcome.statusChanged, outcome.status))
      fetchIntents()
    } catch (error) {
      if (selectedRepoIdRef.current !== requestedRepoId) return
      console.error("Failed to close intent", error)
      setErrorMessage(describeApiError(error))
    }
  }

  const handleReopen = async (intent: Intent) => {
    const requestedRepoId = selectedRepoId
    try {
      const outcome = await reopenIntent(intent.id)
      if (selectedRepoIdRef.current !== requestedRepoId) return
      setOutcomeMessage(formatOutcomeMessage("Reopen", outcome.statusChanged, outcome.status))
      fetchIntents()
    } catch (error) {
      if (selectedRepoIdRef.current !== requestedRepoId) return
      setErrorMessage(describeApiError(error))
    }
  }

  const handleUpdate = async (intent: Intent, updates: { description: string; priority: number }) => {
    const requestedRepoId = selectedRepoId
    try {
      await updateIntent(intent.id, updates)
      if (selectedRepoIdRef.current !== requestedRepoId) return
      fetchIntents()
    } catch (error) {
      if (selectedRepoIdRef.current !== requestedRepoId) return
      console.error("Failed to update intent", error)
      setErrorMessage(describeApiError(error))
    }
  }

  const activeIntents = intents.filter((i) => i.status === "active")
  const closedIntents = intents.filter((i) => i.status === "completed" || i.status === "closed")

  return (
    <motion.div initial="initial" animate="animate" variants={pageTransition} className="space-y-8">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-foreground">Intents</h1>
          <p className="text-muted-foreground mt-1">Track your goals and objectives</p>
        </div>

        <Dialog open={isDialogOpen} onOpenChange={setIsDialogOpen}>
          <DialogTrigger asChild>
              <Button>
                <Plus className="h-4 w-4" />
                New Intent
              </Button>
          </DialogTrigger>
          <DialogContent className="sm:max-w-[425px]">
            <form onSubmit={handleSubmit}>
              <DialogHeader>
                <DialogTitle>Create New Intent</DialogTitle>
                <DialogDescription>
                  Define a new goal or objective for the system to track.
                </DialogDescription>
              </DialogHeader>
              <div className="grid gap-4 py-4">
                <div className="grid gap-2">
                  <Label htmlFor="description">Description</Label>
                  <Input
                    id="description"
                    value={newDescription}
                    onChange={(e) => setNewDescription(e.target.value)}
                    placeholder="e.g., Optimize database queries"
                    className="col-span-3"
                  />
                </div>
                <div className="grid gap-2">
                  <div className="flex justify-between">
                    <Label htmlFor="priority">Priority</Label>
                    <span className="text-sm text-muted-foreground">{newPriority[0]}/10</span>
                  </div>
                  <Slider
                    id="priority"
                    value={newPriority}
                    min={1}
                    max={10}
                    step={1}
                    onValueChange={setNewPriority}
                  />
                </div>
              </div>
              <DialogFooter>
                <Button type="submit" disabled={isSubmitting}>
                  {isSubmitting ? "Creating..." : "Create Intent"}
                </Button>
              </DialogFooter>
            </form>
          </DialogContent>
        </Dialog>
      </div>

      {errorMessage ? (
        <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-4 text-sm text-muted-foreground">
          <div className="flex items-start gap-2">
            <AlertTriangle className="mt-0.5 h-4 w-4 text-destructive" />
            <span>{errorMessage}</span>
          </div>
        </div>
      ) : null}

      {outcomeMessage ? (
        <div className="rounded-lg border border-primary/30 bg-primary/5 p-4 text-sm text-muted-foreground" role="status">
          {outcomeMessage}
        </div>
      ) : null}

      {/* Two Column Layout */}
      <div className="grid gap-8 md:grid-cols-2">
        <IntentsColumn
          title="Active intents"
          intents={activeIntents}
          type="active"
          onComplete={handleComplete}
          onClose={handleClose}
          onUpdate={handleUpdate}
        />
        <IntentsColumn
          title="Recorded outcomes"
          intents={closedIntents}
          type="completed"
          onUpdate={handleUpdate}
          onReopen={handleReopen}
        />
      </div>
    </motion.div>
  )
}

export default function IntentsPage() {
  return (
    <Suspense fallback={null}>
      <IntentsContent />
    </Suspense>
  )
}

function formatOutcomeMessage(
  outcomeName: "Completion" | "Close" | "Reopen",
  statusChanged: boolean,
  status: Intent["status"],
): string {
  if (statusChanged) {
    return `${outcomeName} outcome recorded and the backend changed the authoritative status to ${status}.`
  }
  return `${outcomeName} outcome recorded. The authoritative intent remains ${status} until its workflow authority changes it.`
}
