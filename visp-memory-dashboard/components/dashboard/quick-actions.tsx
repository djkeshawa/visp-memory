"use client"

import { useState } from "react"
import { motion } from "framer-motion"
import { AlertTriangle, Plus, Search } from "lucide-react"
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
import { Textarea } from "@/components/ui/textarea"
import { createMemory, describeApiError } from "@/lib/api"
import { projectHref, useSelectedProjectId } from "@/lib/project-selection"
import Link from "next/link"

interface QuickActionsProps {
  /** Called after a memory is created so the parent can refetch its data. */
  onMemoryCreated?: () => void
}

export function QuickActions({ onMemoryCreated }: QuickActionsProps = {}) {
  const selectedRepoId = useSelectedProjectId()
  const [isDialogOpen, setIsDialogOpen] = useState(false)
  const [content, setContent] = useState("")
  const [category, setCategory] = useState("note")
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  const handleCreateMemory = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!content.trim()) return

    setIsSubmitting(true)
    try {
      await createMemory(content, category, [], selectedRepoId)
      setErrorMessage(null)
      setContent("")
      setCategory("note")
      setIsDialogOpen(false)
      // The dashboard fetches its data client-side, so trigger the parent's
      // refetch (router.refresh() would not re-run that client fetch).
      onMemoryCreated?.()
    } catch (error) {
      console.error("Failed to create memory", error)
      setErrorMessage(describeApiError(error))
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <div className="glass rounded-lg p-5">
      <h3 className="text-sm font-semibold text-foreground mb-4">Quick Actions</h3>
      <div className="space-y-3">
        <Dialog open={isDialogOpen} onOpenChange={setIsDialogOpen}>
          <DialogTrigger asChild>
            <motion.div whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}>
              <Button className="w-full">
                <Plus className="h-4 w-4 mr-2" />
                New Memory
              </Button>
            </motion.div>
          </DialogTrigger>
          <DialogContent>
            <form onSubmit={handleCreateMemory}>
              <DialogHeader>
                <DialogTitle>Create New Memory</DialogTitle>
                <DialogDescription>
                  Manually add a piece of knowledge or an event to the system.
                </DialogDescription>
              </DialogHeader>
              <div className="grid gap-4 py-4">
                {errorMessage ? (
                  <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-muted-foreground">
                    <div className="flex items-start gap-2">
                      <AlertTriangle className="mt-0.5 h-4 w-4 text-destructive" />
                      <span>{errorMessage}</span>
                    </div>
                  </div>
                ) : null}
                <div className="grid gap-2">
                  <Label htmlFor="category">Category</Label>
                  <Input
                    id="category"
                    value={category}
                    onChange={(e) => setCategory(e.target.value)}
                    placeholder="e.g., meeting_note, decision"
                  />
                </div>
                <div className="grid gap-2">
                  <Label htmlFor="content">Content</Label>
                  <Textarea
                    id="content"
                    value={content}
                    onChange={(e) => setContent(e.target.value)}
                    placeholder="What would you like to remember?"
                    className="min-h-[100px]"
                  />
                </div>
              </div>
              <DialogFooter>
                <Button type="submit" disabled={isSubmitting}>
                  {isSubmitting ? "Saving..." : "Save Memory"}
                </Button>
              </DialogFooter>
            </form>
          </DialogContent>
        </Dialog>

        <motion.div whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}>
          <Button asChild variant="secondary" className="w-full">
            <Link href={projectHref("/recall", selectedRepoId)}>
              <Search className="h-4 w-4 mr-2" />
              Search
            </Link>
          </Button>
        </motion.div>
      </div>
    </div>
  )
}
