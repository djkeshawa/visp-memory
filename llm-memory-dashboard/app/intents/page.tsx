"use client"

import { useEffect, useState } from "react"
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
import { createIntent, describeApiError, getActiveIntents } from "@/lib/api"
import { pageTransition } from "@/lib/animations"
import type { Intent } from "@/lib/types"

export default function IntentsPage() {
  const [intents, setIntents] = useState<Intent[]>([])
  const [isDialogOpen, setIsDialogOpen] = useState(false)
  const [newDescription, setNewDescription] = useState("")
  const [newPriority, setNewPriority] = useState([5])
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  useEffect(() => {
    fetchIntents()
  }, [])

  const fetchIntents = () => {
    getActiveIntents()
      .then((data) => {
        setIntents(data)
        setErrorMessage(null)
      })
      .catch((error) => {
        console.error(error)
        setErrorMessage(describeApiError(error))
      })
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!newDescription.trim()) return

    setIsSubmitting(true)
    try {
      await createIntent(newDescription, newPriority[0])
      setErrorMessage(null)
      setNewDescription("")
      setNewPriority([5])
      setIsDialogOpen(false)
      fetchIntents()
    } catch (error) {
      console.error("Failed to create intent", error)
      setErrorMessage(describeApiError(error))
    } finally {
      setIsSubmitting(false)
    }
  }

  const activeIntents = intents.filter((i) => i.status === "active")
  const completedIntents = intents.filter((i) => i.status === "completed")

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
            <motion.div whileHover={{ scale: 1.05 }} whileTap={{ scale: 0.95 }}>
              <Button className="bg-gradient-to-r from-blue-500 to-indigo-600 text-white shadow-md hover:shadow-lg transition-shadow">
                <Plus className="h-4 w-4 mr-2" />
                New Intent
              </Button>
            </motion.div>
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

      {/* Two Column Layout */}
      <div className="grid gap-8 md:grid-cols-2">
        <IntentsColumn title="Active" intents={activeIntents} type="active" />
        <IntentsColumn title="Completed" intents={completedIntents} type="completed" />
      </div>
    </motion.div>
  )
}
