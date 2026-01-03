"use client"

import { useState } from "react"
import { motion } from "framer-motion"
import { Check, CheckCircle2, Circle, Edit3, Sparkles, Undo2, X } from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Slider } from "@/components/ui/slider"
import { staggerItem } from "@/lib/animations"
import type { Intent } from "@/lib/types"
import { cn } from "@/lib/utils"

interface IntentCardProps {
  intent: Intent
  onComplete?: (intent: Intent) => void
  onClose?: (intent: Intent) => void
  onUpdate?: (intent: Intent, updates: { description: string; priority: number }) => void
  onReopen?: (intent: Intent) => void
}

export function IntentCard({ intent, onComplete, onClose, onUpdate, onReopen }: IntentCardProps) {
  const isCompleted = intent.status === "completed"
  const isClosed = intent.status === "closed"
  const [isEditing, setIsEditing] = useState(false)
  const [description, setDescription] = useState(intent.description)
  const [priority, setPriority] = useState([priorityToSlider(intent.priorityValue)])

  const submitUpdate = () => {
    if (!description.trim()) return
    onUpdate?.(intent, { description: description.trim(), priority: priority[0] })
    setIsEditing(false)
  }

  return (
    <>
      <motion.div
        variants={staggerItem}
        whileHover={{ x: 4 }}
        className={cn(
          "glass flex items-start gap-4 rounded-lg p-4 transition-all",
          (isCompleted || isClosed) && "opacity-75",
        )}
      >
        {isCompleted || isClosed ? (
          <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-success" />
        ) : (
          <Circle className="mt-0.5 h-5 w-5 shrink-0 text-intent" />
        )}
        <div className="min-w-0 flex-1">
          <p
            className={cn(
              "text-sm font-medium text-foreground",
              (isCompleted || isClosed) && "line-through text-muted-foreground",
            )}
          >
            {intent.description}
          </p>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <p className="text-xs capitalize text-muted-foreground">{intent.priority} priority</p>
            {isClosed ? <p className="text-xs text-muted-foreground">closed</p> : null}
            {intent.context?.completed_automatically ? <span className="inline-flex items-center gap-1 text-xs text-primary"><Sparkles className="h-3 w-3" />Completed automatically · {Math.round(Number(intent.context?.completion_evaluation?.confidence || 0) * 100)}%</span> : null}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {onUpdate ? (
            <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => setIsEditing(true)}>
              <Edit3 className="h-4 w-4" />
            </Button>
          ) : null}
          {!isCompleted && !isClosed && onComplete ? (
            <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => onComplete(intent)}>
              <Check className="h-4 w-4" />
            </Button>
          ) : null}
          {!isCompleted && !isClosed && onClose ? (
            <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => onClose(intent)}>
              <X className="h-4 w-4" />
            </Button>
          ) : null}
          {(isCompleted || isClosed) && onReopen ? (
            <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => onReopen(intent)} title="Reopen intent">
              <Undo2 className="h-4 w-4" />
            </Button>
          ) : null}
        </div>
      </motion.div>

      <Dialog open={isEditing} onOpenChange={setIsEditing}>
        <DialogContent className="sm:max-w-[425px]">
          <DialogHeader>
            <DialogTitle>Update Intent</DialogTitle>
          </DialogHeader>
          <div className="grid gap-4 py-4">
            <div className="grid gap-2">
              <Label htmlFor={`intent-description-${intent.id}`}>Description</Label>
              <Input
                id={`intent-description-${intent.id}`}
                value={description}
                onChange={(event) => setDescription(event.target.value)}
              />
            </div>
            <div className="grid gap-2">
              <div className="flex justify-between">
                <Label htmlFor={`intent-priority-${intent.id}`}>Priority</Label>
                <span className="text-sm text-muted-foreground">{priority[0]}/10</span>
              </div>
              <Slider
                id={`intent-priority-${intent.id}`}
                value={priority}
                min={1}
                max={10}
                step={1}
                onValueChange={setPriority}
              />
            </div>
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setIsEditing(false)}>
              Cancel
            </Button>
            <Button type="button" onClick={submitUpdate}>
              Save
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}

function priorityToSlider(priority?: number): number {
  if (!priority) return 5
  if (priority >= 3) return 9
  if (priority >= 2) return 5
  return 2
}
