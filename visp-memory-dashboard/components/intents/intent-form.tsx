"use client"

import type { FormEvent } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"

/** Priority choices map onto the 1-10 scale `createIntent` expects (low 1-3, medium 4-7, high 8-10). */
export const PRIORITY_CHOICES = [
  { label: "Medium", value: 5 },
  { label: "High", value: 9 },
  { label: "Low", value: 2 },
] as const

export function IntentForm({ description, priority, submitting, onDescription, onPriority, onSubmit }: {
  description: string
  priority: number
  submitting: boolean
  onDescription: (value: string) => void
  onPriority: (value: number) => void
  onSubmit: (event: FormEvent) => void
}) {
  return (
    <form aria-label="Create intent" onSubmit={onSubmit} className="surface flex flex-wrap items-end gap-3 rounded-2xl p-4">
      <div className="flex min-w-0 flex-1 basis-72 flex-col gap-1.5">
        <Label htmlFor="description" className="font-semibold">New intent</Label>
        <Input id="description" value={description} onChange={(event) => onDescription(event.target.value)} placeholder="e.g. Optimise recall queries for large stores" />
      </div>
      <div className="flex flex-1 basis-36 flex-col gap-1.5 sm:max-w-40">
        <Label htmlFor="priority" className="font-semibold">Priority</Label>
        <select id="priority" value={priority} onChange={(event) => onPriority(Number(event.target.value))} className="h-11 rounded-xl border border-input bg-card px-3 text-sm">
          {PRIORITY_CHOICES.map((choice) => <option key={choice.label} value={choice.value}>{choice.label}</option>)}
        </select>
      </div>
      <Button type="submit" size="lg" disabled={submitting || !description.trim()} className="w-full sm:w-auto">
        {submitting ? "Creating..." : "Create intent"}
      </Button>
    </form>
  )
}
