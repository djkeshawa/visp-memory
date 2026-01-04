"use client"

import { useState } from "react"
import { motion } from "framer-motion"
import { Plus, Search } from "lucide-react"
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
import { createMemory } from "@/lib/api"
import { useRouter } from "next/navigation"

export function QuickActions() {
  const router = useRouter()
  const [isDialogOpen, setIsDialogOpen] = useState(false)
  const [content, setContent] = useState("")
  const [category, setCategory] = useState("note")
  const [isSubmitting, setIsSubmitting] = useState(false)

  const handleCreateMemory = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!content.trim()) return

    setIsSubmitting(true)
    try {
      await createMemory(content, category, [])
      setContent("")
      setCategory("note")
      setIsDialogOpen(false)
      window.location.reload() // Simple reload to refresh lists
    } catch (error) {
      console.error("Failed to create memory", error)
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <div className="glass rounded-xl p-5">
      <h3 className="text-sm font-semibold text-foreground mb-4">Quick Actions</h3>
      <div className="space-y-3">
        <Dialog open={isDialogOpen} onOpenChange={setIsDialogOpen}>
          <DialogTrigger asChild>
            <motion.div whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}>
              <Button className="w-full bg-gradient-to-r from-blue-500 to-indigo-600 text-white shadow-md hover:shadow-lg transition-shadow">
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
          <Button variant="secondary" className="w-full" onClick={() => router.push("/recall")}>
            <Search className="h-4 w-4 mr-2" />
            Search
          </Button>
        </motion.div>
      </div>
    </div>
  )
}
