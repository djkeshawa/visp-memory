"use client"

import { FormEvent, useEffect, useState } from "react"
import { Button } from "@/components/ui/button"
import { PageHeader } from "@/components/strata/primitives"
import {
  archiveProject,
  createProject,
  describeApiError,
  getProjectPurgePreview,
  getProjects,
  purgeProject,
  restoreProject,
} from "@/lib/api"
import { useRefreshProjectScopes, useSelectedProjectId } from "@/lib/project-selection"
import type { Project } from "@/lib/types"
import { CreateProjectForm } from "./_components/create-project-form"
import { ProjectTable } from "./_components/project-table"
import { PurgeDialog } from "./_components/purge-dialog"
import { PurgeSection } from "./_components/purge-section"

export default function ProjectsPage() {
  const refreshProjectScopes = useRefreshProjectScopes()
  const currentId = useSelectedProjectId()
  const [projects, setProjects] = useState<Project[]>([])
  const [includeArchived, setIncludeArchived] = useState(false)
  const [name, setName] = useState("")
  const [id, setId] = useState("")
  const [description, setDescription] = useState("")
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [purgeTarget, setPurgeTarget] = useState<Project | null>(null)
  const [purgePreview, setPurgePreview] = useState<Record<string, number | string> | null>(null)
  const [confirmation, setConfirmation] = useState("")

  const load = async () => {
    setLoading(true)
    try {
      setProjects(await getProjects(includeArchived))
      setError(null)
    } catch (loadError) {
      setError(describeApiError(loadError))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { void load() }, [includeArchived])

  const create = async (event: FormEvent) => {
    event.preventDefault()
    if (!name.trim()) return
    setSaving(true)
    try {
      await createProject({ name, id: id.trim() || undefined, description: description.trim() || undefined })
      setName("")
      setId("")
      setDescription("")
      await Promise.all([load(), refreshProjectScopes()])
    } catch (saveError) {
      setError(describeApiError(saveError))
    } finally {
      setSaving(false)
    }
  }

  const archive = async (project: Project) => {
    try {
      await archiveProject(project.id)
      await Promise.all([load(), refreshProjectScopes()])
    } catch (actionError) {
      setError(describeApiError(actionError))
    }
  }

  const restore = async (project: Project) => {
    try {
      await restoreProject(project.id)
      await Promise.all([load(), refreshProjectScopes()])
    } catch (actionError) {
      setError(describeApiError(actionError))
    }
  }

  const openPurge = async (project: Project) => {
    setPurgeTarget(project)
    setConfirmation("")
    setPurgePreview(null)
    try {
      setPurgePreview(await getProjectPurgePreview(project.id))
    } catch (previewError) {
      setError(describeApiError(previewError))
    }
  }

  const purge = async () => {
    if (!purgeTarget || confirmation !== purgeTarget.id) return
    setSaving(true)
    try {
      await purgeProject(purgeTarget.id)
      setPurgeTarget(null)
      await Promise.all([load(), refreshProjectScopes()])
    } catch (purgeError) {
      setError(describeApiError(purgeError))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        eyebrow="Repository scopes keep each project's memory separate"
        title="Projects"
        actions={
          <>
            <label className="inline-flex min-h-11 items-center gap-2.5 text-[13px] text-muted-foreground">
              <input type="checkbox" className="h-[18px] w-[18px]" checked={includeArchived} onChange={(event) => setIncludeArchived(event.target.checked)} />
              Show archived
            </label>
            <Button variant="outline" className="h-10" onClick={() => void load()} disabled={loading}>Refresh</Button>
          </>
        }
      />

      {error ? <p className="rounded-xl border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive" role="alert">{error}</p> : null}

      <div className="flex flex-wrap items-start gap-6">
        <ProjectTable projects={projects} loading={loading} currentId={currentId} onArchive={(project) => void archive(project)} onRestore={(project) => void restore(project)} />
        <aside className="flex min-w-0 flex-[1_1_18rem] flex-col gap-4">
          <CreateProjectForm name={name} id={id} description={description} saving={saving} onName={setName} onId={setId} onDescription={setDescription} onSubmit={create} />
          <PurgeSection projects={projects} onPurge={(project) => void openPurge(project)} />
        </aside>
      </div>

      <PurgeDialog
        target={purgeTarget}
        preview={purgePreview}
        confirmation={confirmation}
        saving={saving}
        onConfirmation={setConfirmation}
        onCancel={() => setPurgeTarget(null)}
        onPurge={() => void purge()}
      />
    </div>
  )
}
