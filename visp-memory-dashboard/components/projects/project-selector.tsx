"use client"

import { useId } from "react"
import { FolderGit2 } from "lucide-react"
import { useProjectScopes, useSelectProject, useSelectedProjectId } from "@/lib/project-selection"

export function ProjectSelector() {
  const labelId = useId()
  const selectedRepoId = useSelectedProjectId()
  const selectProject = useSelectProject()
  const { projects, loadError } = useProjectScopes()

  return (
    <div className="space-y-2 px-5">
      <div id={labelId} className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
        <FolderGit2 className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
        Project
      </div>
      <select
        aria-labelledby={labelId}
        value={selectedRepoId ?? ""}
        onChange={(event) => selectProject(event.target.value)}
        disabled={projects.length === 0}
        className="h-10 w-full rounded-xl border border-border bg-card px-3 text-sm text-foreground outline-none transition-colors focus:border-ring focus:ring-2 focus:ring-ring/20 disabled:cursor-not-allowed disabled:opacity-60"
      >
        {projects.length === 0 ? (
          <option value="">No projects found</option>
        ) : (
          projects.map((project) => (
            <option key={project.id} value={project.id}>
              {project.name}
            </option>
          ))
        )}
      </select>
      {loadError ? <p role="alert" className="text-xs text-destructive">{loadError}</p> : null}
    </div>
  )
}
