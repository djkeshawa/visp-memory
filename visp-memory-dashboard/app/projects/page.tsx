"use client"

import { FormEvent, useEffect, useState } from "react"
import { Archive, FolderGit2, Plus, RefreshCw, RotateCcw, Trash2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  archiveProject,
  createProject,
  describeApiError,
  getProjectPurgePreview,
  getProjects,
  purgeProject,
  restoreProject,
} from "@/lib/api"
import { useRefreshProjectScopes } from "@/lib/project-selection"
import type { Project } from "@/lib/types"

export default function ProjectsPage() {
  const refreshProjectScopes = useRefreshProjectScopes()
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
    <div className="space-y-7">
      <header className="flex flex-col gap-3 border-b border-border pb-5 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-foreground">Projects</h1>
          <p className="mt-1 text-sm text-muted-foreground">Manage repository scopes and their stored memory.</p>
        </div>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 text-sm text-muted-foreground"><input type="checkbox" checked={includeArchived} onChange={(event) => setIncludeArchived(event.target.checked)} />Show archived</label>
          <Button size="sm" variant="outline" onClick={() => void load()} disabled={loading}><RefreshCw className={loading ? "h-4 w-4 animate-spin" : "h-4 w-4"} /><span>Refresh</span></Button>
        </div>
      </header>

      {error ? <p className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive" role="alert">{error}</p> : null}

      <section className="border-b border-border pb-7" aria-labelledby="create-project-heading">
        <div className="flex items-center gap-2"><Plus className="h-5 w-5 text-highlight" /><h2 id="create-project-heading" className="text-lg font-semibold">Create project</h2></div>
        <form onSubmit={create} className="mt-4 grid gap-4 md:grid-cols-[1fr_0.8fr_1.4fr_auto] md:items-end">
          <div className="space-y-2"><Label htmlFor="project-name">Name</Label><Input id="project-name" value={name} onChange={(event) => setName(event.target.value)} required /></div>
          <div className="space-y-2"><Label htmlFor="project-id">ID</Label><Input id="project-id" value={id} onChange={(event) => setId(event.target.value)} placeholder="generated-from-name" /></div>
          <div className="space-y-2"><Label htmlFor="project-description">Description</Label><Input id="project-description" value={description} onChange={(event) => setDescription(event.target.value)} /></div>
          <Button type="submit" disabled={saving}><Plus className="h-4 w-4" /><span>Create</span></Button>
        </form>
      </section>

      <section aria-labelledby="project-list-heading">
        <h2 id="project-list-heading" className="text-lg font-semibold">Repository scopes</h2>
        <div className="mt-3 overflow-x-auto rounded-lg border border-border">
          <table className="w-full min-w-[44rem] text-left text-sm">
            <thead className="bg-secondary/60 text-xs text-muted-foreground"><tr><th className="px-4 py-3">Project</th><th className="px-4 py-3">ID</th><th className="px-4 py-3">Status</th><th className="px-4 py-3">Created</th><th className="px-4 py-3 text-right">Actions</th></tr></thead>
            <tbody className="divide-y divide-border">
              {projects.map((project) => (
                <tr key={project.id}>
                  <td className="px-4 py-3"><div className="flex items-center gap-3"><FolderGit2 className="h-4 w-4 text-highlight" /><div><p className="font-medium text-foreground">{project.name}</p><p className="max-w-sm truncate text-xs text-muted-foreground">{project.description || "No description"}</p></div></div></td>
                  <td className="px-4 py-3"><code className="text-xs">{project.id}</code></td>
                  <td className="px-4 py-3"><span className={project.status === "active" ? "text-success" : "text-intent"}>{project.status}</span></td>
                  <td className="px-4 py-3 text-muted-foreground">{new Date(project.createdAt).toLocaleDateString()}</td>
                  <td className="px-4 py-3"><div className="flex justify-end gap-1">{project.status === "active" ? <Button size="sm" variant="ghost" onClick={() => void archive(project)} title="Archive"><Archive className="h-4 w-4" /></Button> : <Button size="sm" variant="ghost" onClick={() => void restore(project)} title="Restore"><RotateCcw className="h-4 w-4" /></Button>}<Button size="sm" variant="ghost" onClick={() => void openPurge(project)} title="Permanently purge"><Trash2 className="h-4 w-4 text-destructive" /></Button></div></td>
                </tr>
              ))}
              {!loading && projects.length === 0 ? <tr><td colSpan={5} className="px-4 py-9 text-center text-muted-foreground">No projects in this view.</td></tr> : null}
            </tbody>
          </table>
        </div>
      </section>

      <Dialog open={Boolean(purgeTarget)} onOpenChange={(open) => { if (!open) setPurgeTarget(null) }}>
        <DialogContent>
          <DialogHeader><DialogTitle>Permanently purge project</DialogTitle><DialogDescription>This removes the project and scoped memories after creating a server-side backup. It cannot be undone from the dashboard.</DialogDescription></DialogHeader>
          {purgePreview ? <dl className="grid grid-cols-3 gap-3 rounded-md border border-border bg-secondary/40 p-3 text-sm"><div><dt className="text-muted-foreground">Memories</dt><dd className="font-semibold">{purgePreview.memories}</dd></div><div><dt className="text-muted-foreground">Intents</dt><dd className="font-semibold">{purgePreview.intents}</dd></div><div><dt className="text-muted-foreground">Links</dt><dd className="font-semibold">{purgePreview.relationships}</dd></div></dl> : null}
          <div className="space-y-2"><Label htmlFor="purge-confirmation">Type {purgeTarget?.id} to confirm</Label><Input id="purge-confirmation" value={confirmation} onChange={(event) => setConfirmation(event.target.value)} autoComplete="off" /></div>
          <DialogFooter><Button variant="outline" onClick={() => setPurgeTarget(null)}>Cancel</Button><Button variant="destructive" onClick={() => void purge()} disabled={saving || confirmation !== purgeTarget?.id}><Trash2 className="h-4 w-4" /><span>Purge project</span></Button></DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
