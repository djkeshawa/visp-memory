"use client"

import Link from "next/link"
import { Suspense, useEffect, useRef, useState } from "react"
import { PageHeader } from "@/components/strata/primitives"
import { Findings } from "@/components/dreaming/findings"
import { ScheduleStrip } from "@/components/dreaming/schedule-strip"
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
  return <div className="mx-auto flex max-w-4xl flex-col gap-6">
    <PageHeader eyebrow="Memory care · dreaming" title="Review"
      description="Give your memories time to settle. Combine exact duplicate notes, discover connections, and review what may be outdated. These are suggestions to check, not stored facts."
      actions={<Link className="text-sm font-medium text-highlight underline-offset-4 hover:underline" href={projectHref("/memories", repoId)}>Browse library</Link>} />
    {!repoId && <p className="text-sm text-muted-foreground">Select a project to configure its dreaming cycle.</p>}
    {loading && <p role="status" className="text-sm text-muted-foreground">Loading dreaming…</p>}
    {error && <p role="alert" className="rounded-xl border border-destructive/30 bg-destructive/5 p-4 text-sm">{error}</p>}
    {data && <>
      <ScheduleStrip settings={data.settings} lastRun={data.runs[0]} interval={interval} busy={busy} onInterval={setInterval}
        onSchedule={(enabled) => void perform("/schedule", "PUT", { enabled, interval_hours: interval })}
        onPreview={() => void perform("/preview", "GET")} onRun={() => void perform("/run")} />
      {data.runs.length > 0 && <div className="flex flex-wrap items-center gap-3">
        <label htmlFor="dream-history" className="text-sm font-medium">Cycle history</label>
        <select id="dream-history" value={selectedRun || data.runs[0]?.id} onChange={(event) => { setSelectedRun(event.target.value); setPreview(null) }} className="h-10 max-w-full rounded-xl border border-input bg-card px-3 text-sm">
          {data.runs.map((item) => <option key={item.id} value={item.id}>{new Date(item.created_at!).toLocaleString()} · {item.scheduled ? "Scheduled" : "Manual"}</option>)}
        </select>
      </div>}
      {run ? <Findings run={run} preview={Boolean(preview)} busy={busy}
        onReview={(current, item, decision) => void perform(`/runs/${current.id}/proposals/${item.id}`, "POST", { decision })}
        onUndo={(item) => void perform(`/actions/${item.action_id}/undo`)} />
        : <p className="rounded-2xl border border-dashed border-border p-8 text-center text-sm text-muted-foreground">No cycles yet. Preview the next cycle to see what dreaming would find.</p>}
    </>}
  </div>
}

export default function DreamingPage() {
  return <Suspense fallback={null}><DreamingContent /></Suspense>
}
