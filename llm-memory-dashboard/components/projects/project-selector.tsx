"use client"

import { FolderGit2 } from "lucide-react"
import { useProjectScopes, useSelectProject, useSelectedProjectId } from "@/lib/project-selection"

export function ProjectSelector() {
  const selectedRepoId = useSelectedProjectId()
  const selectProject = useSelectProject()
  const { projects, loadError } = useProjectScopes()

  return (
    <div className="space-y-2 px-4">
      <div className="flex items-center gap-2 text-sm font-medium text-foreground">
        <FolderGit2 className="h-4 w-4 text-muted-foreground" />
        Project
      </div>
      <select
        value={selectedRepoId ?? ""}
        onChange={(event) => selectProject(event.target.value)}
        disabled={projects.length === 0}
        className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm text-foreground outline-none transition-colors focus:border-ring focus:ring-2 focus:ring-ring/20 disabled:cursor-not-allowed disabled:opacity-60"
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
      {loadError ? <p className="text-xs text-destructive">{loadError}</p> : null}
    </div>
  )
}
