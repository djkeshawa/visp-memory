"use client"

import { useEffect, useRef, useState } from "react"
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
import { Textarea } from "@/components/ui/textarea"
import { createMemory, describeApiError } from "@/lib/api"
import { useSelectedProjectId } from "@/lib/project-selection"

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
  const selectedRepoIdRef = useRef(selectedRepoId)
  const saveGenerationRef = useRef(0)
  selectedRepoIdRef.current = selectedRepoId

  useEffect(() => {
    ++saveGenerationRef.current
    setIsDialogOpen(false)
    setContent("")
    setCategory("note")
    setIsSubmitting(false)
    setErrorMessage(null)
  }, [selectedRepoId])

  const handleCreateMemory = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!content.trim()) return

    const requestedRepoId = selectedRepoId
    const generation = ++saveGenerationRef.current
    const isCurrent = () => generation === saveGenerationRef.current && selectedRepoIdRef.current === requestedRepoId
    setIsSubmitting(true)
    try {
      await createMemory(content, category, [], requestedRepoId)
      if (!isCurrent()) return
      setErrorMessage(null)
      setContent("")
      setCategory("note")
      setIsDialogOpen(false)
      // The dashboard fetches its data client-side, so trigger the parent's
      // refetch (router.refresh() would not re-run that client fetch).
      onMemoryCreated?.()
    } catch (error) {
      if (!isCurrent()) return
      console.error("Failed to create memory", error)
      setErrorMessage(describeApiError(error))
    } finally {
      if (isCurrent()) setIsSubmitting(false)
    }
  }

  return (
    <div>
      <Dialog open={isDialogOpen} onOpenChange={setIsDialogOpen}>
        <DialogTrigger asChild>
          <Button className="h-10 px-4">
            <Plus className="h-4 w-4" />
            New memory
          </Button>
        </DialogTrigger>
        <DialogContent>
          <form onSubmit={handleCreateMemory}>
            <DialogHeader>
              <DialogTitle>New memory</DialogTitle>
              <DialogDescription>
                Save a decision, a useful fact, or a lesson for later search.
                Dashboard notes are excluded from task briefs and automatic context because they enter through the API.
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
              <Button type="submit" disabled={isSubmitting || !content.trim()}>
                {isSubmitting ? "Saving..." : "Save memory"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

    </div>
  )
}
