"use client"

import { useEffect, useState } from "react"
import { FolderGit2 } from "lucide-react"
import { describeApiError, getProjectScopes, getRuntimeStatus } from "@/lib/api"
import { useSelectProject, useSelectedProjectId } from "@/lib/project-selection"
import type { ProjectScope } from "@/lib/types"

export function ProjectSelector() {
  const selectedRepoId = useSelectedProjectId()
  const selectProject = useSelectProject()
  const [projects, setProjects] = useState<ProjectScope[]>([])
  const [loadError, setLoadError] = useState<string | null>(null)

  // Load the list of selectable projects once. Selection/persistence is owned entirely by the
  // SelectedProjectProvider, so this effect no longer navigates or auto-selects — it only fills
  // the dropdown options (deps [] so populating the list never re-triggers a selection).
  useEffect(() => {
    let isMounted = true

    async function loadProjects() {
      try {
        const [scopes, runtime] = await Promise.all([getProjectScopes(), getRuntimeStatus()])
        if (!isMounted) return

        const byId = new Map(scopes.map((scope) => [scope.id, scope]))
        if (runtime.repoId && !byId.has(runtime.repoId)) {
          byId.set(runtime.repoId, {
            id: runtime.repoId,
            name: runtime.repoId,
            registered: false,
          })
        }

        const nextProjects = Array.from(byId.values()).sort((a, b) => a.name.localeCompare(b.name))
        setProjects(nextProjects)
        setLoadError(null)
      } catch (error) {
        if (!isMounted) return
        setLoadError(describeApiError(error))
      }
    }

    loadProjects()
    return () => {
      isMounted = false
    }
  }, [])

  return (
    <div className="space-y-2 px-4">
      <div className="flex items-center gap-2 text-sm font-medium text-foreground">
        <FolderGit2 className="h-4 w-4 text-muted-foreground" />
        Project
      </div>
      <select
        value={selectedRepoId ?? projects[0]?.id ?? ""}
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
