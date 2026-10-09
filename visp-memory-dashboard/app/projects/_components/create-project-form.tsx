import type { FormEvent } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"

interface CreateProjectFormProps {
  name: string
  id: string
  description: string
  saving: boolean
  onName: (value: string) => void
  onId: (value: string) => void
  onDescription: (value: string) => void
  onSubmit: (event: FormEvent) => void
}

export function CreateProjectForm({ name, id, description, saving, onName, onId, onDescription, onSubmit }: CreateProjectFormProps) {
  return (
    <form onSubmit={onSubmit} aria-labelledby="create-project-heading" className="surface flex flex-col gap-3.5 rounded-2xl p-5">
      <h2 id="create-project-heading" className="text-[15px] font-semibold">Create project</h2>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="project-name" className="font-semibold">Name</Label>
        <Input id="project-name" value={name} onChange={(event) => onName(event.target.value)} placeholder="visp-cockpit" required />
      </div>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="project-id" className="font-semibold">Project id</Label>
        <Input id="project-id" value={id} onChange={(event) => onId(event.target.value)} placeholder="generated-from-name" className="font-mono text-[13px]" />
      </div>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="project-description" className="font-semibold">Description <span className="font-normal text-muted-foreground">· optional</span></Label>
        <Textarea id="project-description" rows={2} value={description} onChange={(event) => onDescription(event.target.value)} className="min-h-0 resize-y rounded-xl" />
      </div>
      <Button type="submit" size="lg" disabled={saving} className="h-11">Create project</Button>
    </form>
  )
}
