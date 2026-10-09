"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Slider } from "@/components/ui/slider"
import type { Intent } from "@/lib/types"

export function priorityToSlider(priority?: number): number {
  if (!priority) return 5
  if (priority >= 3) return 9
  if (priority >= 2) return 5
  return 2
}

export function IntentEditDialog({ intent, open, onOpenChange, onSave }: {
  intent: Intent
  open: boolean
  onOpenChange: (open: boolean) => void
  onSave: (updates: { description: string; priority: number }) => void
}) {
  const [description, setDescription] = useState(intent.description)
  const [priority, setPriority] = useState([priorityToSlider(intent.priorityValue)])
  const save = () => {
    if (!description.trim()) return
    onSave({ description: description.trim(), priority: priority[0] })
    onOpenChange(false)
  }
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[425px]">
        <DialogHeader><DialogTitle>Update intent</DialogTitle><DialogDescription>Change the description or priority. Status changes are reported by your workflow.</DialogDescription></DialogHeader>
        <div className="grid gap-4 py-4">
          <div className="grid gap-2">
            <Label htmlFor={`intent-description-${intent.id}`}>Description</Label>
            <Input id={`intent-description-${intent.id}`} value={description} onChange={(event) => setDescription(event.target.value)} />
          </div>
          <div className="grid gap-2">
            <div className="flex justify-between">
              <Label id={`intent-priority-label-${intent.id}`}>Priority</Label>
              <span className="text-sm text-muted-foreground">{priority[0]}/10</span>
            </div>
            <Slider id={`intent-priority-${intent.id}`} value={priority} min={1} max={10} step={1} onValueChange={setPriority} aria-labelledby={`intent-priority-label-${intent.id}`} />
          </div>
        </div>
        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button type="button" onClick={save}>Save</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
