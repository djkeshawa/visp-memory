"use client"

import Link from "next/link"
import { Suspense, useEffect, useRef, useState } from "react"
import { Moon, RefreshCw } from "lucide-react"
import { Button } from "@/components/ui/button"
import { DreamProposalCard } from "@/components/dreaming/proposal"
import { describeApiError, dreamingRequest } from "@/lib/api"
import { projectHref, useSelectedProjectId } from "@/lib/project-selection"
import type { DreamRun, DreamStatus } from "@/lib/dreaming-types"

function DreamingContent() {
  const repoId = useSelectedProjectId()
  const [data, setData] = useState<DreamStatus | null>(null)
  const [preview, setPreview] = useState<DreamRun | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [interval, setInterval] = useState(24)
  const [selectedRun, setSelectedRun] = useState<string | null>(null)
  const generation = useRef(0)
  const selectedRepo = useRef(repoId)
  selectedRepo.current = repoId

  useEffect(() => {
    const current = ++generation.current
    setData(null); setPreview(null); setError(null); setSelectedRun(null); setBusy(false)
    if (!repoId) { setLoading(false); return }
    setLoading(true)
    dreamingRequest<DreamStatus>(repoId).then((value) => {
      if (current !== generation.current) return
      setData(value); setInterval(value.settings.interval_hours)
    }).catch((failure) => { if (current === generation.current) setError(describeApiError(failure)) })
      .finally(() => { if (current === generation.current) setLoading(false) })
    const timer = window.setInterval(() => {
      if (document.visibilityState !== "visible") return
      void dreamingRequest<DreamStatus>(repoId).then((value) => {
        if (current === generation.current) setData(value)
      }).catch(() => {})
    }, 30000)
    return () => window.clearInterval(timer)
  }, [repoId])

  async function perform(action: string, method = "POST", body?: unknown) {
    if (!repoId || busy) return
    const requestedRepo = repoId
    setBusy(true); setError(null)
    try {
      const result = await dreamingRequest<DreamRun>(requestedRepo, action, method, body)
      if (selectedRepo.current !== requestedRepo) return
      if (action === "/preview") setPreview(result)
      else {
        setPreview(null)
        if (action === "/run") setSelectedRun(null)
      }
      const latest = await dreamingRequest<DreamStatus>(requestedRepo)
      if (selectedRepo.current === requestedRepo) setData(latest)
    } catch (failure) {
      if (selectedRepo.current === requestedRepo) setError(describeApiError(failure))
    } finally { if (selectedRepo.current === requestedRepo) setBusy(false) }
  }

  const run = preview || data?.runs.find((item) => item.id === selectedRun) || data?.runs[0]
  const automatic = run?.proposals.filter((p) => p.automatic).length || 0
  return <div className="mx-auto max-w-5xl space-y-7">
    <header className="flex flex-wrap items-start justify-between gap-4">
      <div>
        <p className="mb-2 flex items-center gap-2 text-sm font-medium text-highlight"><Moon className="h-4 w-4" />Memory care</p>
        <h1 className="text-3xl font-semibold">Dreaming</h1>
        <p className="mt-3 max-w-xl text-sm leading-6 text-muted-foreground">Give your memories time to settle. Combine exact duplicate notes, discover connections, and review what may be outdated.</p>
      </div>
      <Link className="text-sm text-highlight underline" href={projectHref("/memories", repoId)}>Browse memories</Link>
    </header>
    {!repoId && <p>Select a project to configure its dreaming cycle.</p>}
    {loading && <p role="status">Loading dreaming…</p>}
    {error && <p role="alert" className="rounded-xl border border-destructive/30 bg-destructive/5 p-4 text-sm">{error}</p>}
    {data && <>
      <section className="rounded-xl border border-border bg-card p-5" aria-label="Dreaming schedule">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <h2 className="font-semibold">{data.settings.enabled ? "Scheduled dreaming is on" : "Scheduled dreaming is paused"}</h2>
            <p className="mt-2 text-sm text-muted-foreground">Runs when due after five quiet minutes of server activity.</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <select aria-label="Dreaming frequency" value={interval} onChange={(event) => setInterval(Number(event.target.value))} className="rounded-md border border-border bg-background px-3 py-2 text-sm" disabled={busy}>
              <option value={6}>Every 6 hours</option><option value={12}>Every 12 hours</option>
              <option value={24}>Daily</option><option value={168}>Weekly</option>
            </select>
            <Button disabled={busy} variant="outline" onClick={() => void perform("/schedule", "PUT", { enabled: true, interval_hours: interval })}>{data.settings.enabled ? "Save schedule" : "Enable dreaming"}</Button>
            {data.settings.enabled && <Button disabled={busy} variant="ghost" onClick={() => void perform("/schedule", "PUT", { enabled: false, interval_hours: interval })}>Pause</Button>}
          </div>
        </div>
        {data.settings.next_run && <p className="mt-3 text-xs text-muted-foreground">Next due: {new Date(data.settings.next_run).toLocaleString()}. Active work may delay the cycle.</p>}
        {data.settings.last_error && <p className="mt-3 text-sm text-destructive" role="alert">{data.settings.last_error}</p>}
      </section>
      <section className="rounded-xl border border-border p-5">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div><h2 className="font-semibold">A quiet cleanup</h2><p className="mt-2 max-w-xl text-sm leading-6 text-muted-foreground">Eligible exact duplicates are merged automatically. Related notes, possible contradictions, and expired facts stay available for review. Originals and evidence are retained.</p></div>
          <div className="flex gap-2"><Button disabled={busy} variant="outline" onClick={() => void perform("/preview", "GET")}>Preview cycle</Button><Button disabled={busy} onClick={() => void perform("/run")}><RefreshCw className={`h-4 w-4 ${busy ? "animate-spin" : ""}`} />{busy ? "Working…" : "Run dreaming now"}</Button></div>
        </div>
        <p className="mt-3 text-xs text-muted-foreground">Uses exact content and shared words. Draft summaries are source excerpts. No model calls or vector index are required.</p>
      </section>
      {data.runs.length > 0 && <div className="flex flex-wrap items-center gap-3">
        <label htmlFor="dream-history" className="text-sm font-medium">Cycle history</label>
        <select id="dream-history" value={selectedRun || data.runs[0]?.id} onChange={(event) => { setSelectedRun(event.target.value); setPreview(null) }} className="max-w-full rounded-md border border-border bg-background px-3 py-2 text-sm">
          {data.runs.map((item) => <option key={item.id} value={item.id}>{new Date(item.created_at!).toLocaleString()} · {item.scheduled ? "Scheduled" : "Manual"}</option>)}
        </select>
      </div>}
      {run ? <section className="space-y-4" aria-label="Dreaming findings">
        <div><h2 className="text-lg font-semibold">{preview ? "Cycle preview" : "Cycle findings"}</h2>
          <p className="mt-1 text-sm text-muted-foreground">{run.scanned} memories examined · {automatic} eligible duplicate groups · {run.proposals.length} findings</p>
          {(run.partial || run.proposal_limit_reached || run.pair_limit_reached) && <p className="mt-2 text-sm text-muted-foreground">This cycle covers a limited batch. Later cycles rotate through the project; matches across separate batches may need manual review.</p>}
        </div>
        {run.proposals.length === 0 && <p className="rounded-xl border border-dashed border-border p-8 text-center text-sm text-muted-foreground">No cleanup or review suggestions in this batch.</p>}
        {run.proposals.map((item) => <DreamProposalCard key={`${run.id || "preview"}-${item.id}`} item={item} busy={busy}
          onReview={run.id ? (decision) => void perform(`/runs/${run.id}/proposals/${item.id}`, "POST", { decision }) : undefined}
          onUndo={item.action_id ? () => void perform(`/actions/${item.action_id}/undo`) : undefined} />)}
      </section> : <p className="rounded-xl border border-dashed border-border p-8 text-center text-sm text-muted-foreground">No cycles yet. Preview the next cycle to see what dreaming would find.</p>}
    </>}
  </div>
}

export default function DreamingPage() {
  return <Suspense fallback={null}><DreamingContent /></Suspense>
}
