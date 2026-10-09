import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import type { Project } from "@/lib/types"

/**
 * The danger zone: pick a project, then open the purge confirmation dialog.
 * The server permits purging active projects (as the dashboard always has), so the
 * picker lists both, archived first, and labels each so the choice is explicit.
 */
export function PurgeSection({ projects, onPurge }: { projects: Project[]; onPurge: (project: Project) => void }) {
  const [selectedId, setSelectedId] = useState("")
  const target = projects.find((project) => project.id === selectedId)
  const ordered = [...projects].sort((a, b) => Number(a.status === "active") - Number(b.status === "active"))

  return (
    <section aria-labelledby="purge-heading" className="flex flex-col gap-2.5 rounded-2xl border border-destructive/40 px-5 py-4">
      <h2 id="purge-heading" className="text-[15px] font-semibold text-destructive">Purge a project</h2>
      <p className="text-[13px] leading-5 text-muted-foreground">Removes the project and its scoped memories after a server-side backup. It cannot be undone from the dashboard. Archived projects are listed first; archive a project instead if you may want it back.</p>
      <Label htmlFor="purge-project" className="sr-only">Purge target</Label>
      <select id="purge-project" value={selectedId} onChange={(event) => setSelectedId(event.target.value)} className="h-11 w-full rounded-xl border border-input bg-card px-3 text-sm">
        <option value="">Choose a project</option>
        {ordered.map((project) => <option key={project.id} value={project.id}>{project.name} ({project.status})</option>)}
      </select>
      <Button variant="outline" disabled={!target} onClick={() => target && onPurge(target)} className="h-10 self-start border-destructive/50 font-semibold text-destructive hover:bg-destructive/10 hover:text-destructive">
        {target ? `Purge ${target.id}…` : "Purge…"}
      </Button>
    </section>
  )
}
