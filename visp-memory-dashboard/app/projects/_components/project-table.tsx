import { Button } from "@/components/ui/button"
import { Pill } from "@/components/strata/primitives"
import { shortDate } from "@/lib/memory-trust"
import type { Project } from "@/lib/types"

interface ProjectTableProps {
  projects: Project[]
  loading: boolean
  currentId: string | null
  onArchive: (project: Project) => void
  onRestore: (project: Project) => void
}

const HEAD = "px-3 py-3 text-xs font-medium text-muted-foreground"

function StatusPill({ project, current }: { project: Project; current: boolean }) {
  if (project.status === "archived") return <Pill tone="neutral">Archived</Pill>
  return current ? <Pill tone="info">Current</Pill> : <Pill tone="success">Active</Pill>
}

export function ProjectTable({ projects, loading, currentId, onArchive, onRestore }: ProjectTableProps) {
  return (
    <section aria-label="Repository scopes" className="surface min-w-0 flex-[999_1_34rem] overflow-hidden rounded-2xl">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[24rem] border-collapse text-left text-sm">
          <thead>
            <tr>
              <th scope="col" className={`${HEAD} pl-5`}>Project</th>
              <th scope="col" className={HEAD}>Status</th>
              <th scope="col" className={`${HEAD} pr-5 text-right`}>Actions</th>
            </tr>
          </thead>
          <tbody>
            {projects.map((project) => (
              <tr key={project.id} className="border-t border-border">
                <td className="py-3.5 pl-5 pr-3 align-top">
                  <span className={project.status === "archived" ? "block font-semibold text-muted-foreground" : "block font-semibold"}>{project.name}</span>
                  <span className="block text-[13px] text-muted-foreground">{project.description || "No description"}</span>
                  <span className="mt-0.5 block font-mono text-xs text-muted-foreground">{project.id} · created {shortDate(project.createdAt) ?? "unknown"}</span>
                </td>
                <td className="px-3 py-3.5 align-top"><StatusPill project={project} current={project.id === currentId} /></td>
                <td className="whitespace-nowrap py-2 pl-3 pr-5 text-right align-top">
                  {project.status === "active" ? (
                    <Button size="sm" variant="outline" className="h-10" onClick={() => onArchive(project)} aria-label={`Archive ${project.name}`}>Archive</Button>
                  ) : (
                    <Button size="sm" variant="outline" className="h-10" onClick={() => onRestore(project)} aria-label={`Restore ${project.name}`}>Restore</Button>
                  )}
                </td>
              </tr>
            ))}
            {!loading && projects.length === 0 ? <tr className="border-t border-border"><td colSpan={3} className="px-4 py-9 text-center text-muted-foreground">No projects in this view.</td></tr> : null}
          </tbody>
        </table>
      </div>
    </section>
  )
}
