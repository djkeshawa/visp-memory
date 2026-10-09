"use client"

import { RefreshCw } from "lucide-react"
import { Button } from "@/components/ui/button"
import type { DreamRun, DreamSettings } from "@/lib/dreaming-types"

const INTERVALS = [
  { hours: 6, label: "6 hours" },
  { hours: 12, label: "12 hours" },
  { hours: 24, label: "Daily" },
  { hours: 168, label: "Weekly" },
]

function lastRunSummary(run?: DreamRun): string | null {
  if (!run) return null
  const merged = run.proposals.filter((item) => item.automatic && item.resolution === "applied").length
  const found = `Found ${run.proposals.length} ${run.proposals.length === 1 ? "proposal" : "proposals"}.`
  return merged ? `${found} ${merged} exact ${merged === 1 ? "duplicate was" : "duplicates were"} merged automatically and can be undone.` : found
}

/** When dreaming last ran and runs next, with the schedule controls and the manual preview and run. */
export function ScheduleStrip({ settings, lastRun, interval, busy, onInterval, onSchedule, onPreview, onRun }: {
  settings: DreamSettings
  lastRun?: DreamRun
  interval: number
  busy: boolean
  onInterval: (hours: number) => void
  onSchedule: (enabled: boolean) => void
  onPreview: () => void
  onRun: () => void
}) {
  const last = lastRun?.created_at ? new Date(lastRun.created_at).toLocaleString() : null
  const next = settings.next_run ? new Date(settings.next_run).toLocaleString() : null
  const summary = lastRunSummary(lastRun)
  return (
    <section aria-label="Dreaming schedule" className="surface flex flex-col gap-4 rounded-2xl px-5 py-4 sm:px-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="min-w-0 flex-1 basis-64">
          <h2 className="font-semibold">{settings.enabled ? "Scheduled dreaming is on" : "Scheduled dreaming is paused"}</h2>
          <p className="mt-0.5 text-sm text-muted-foreground">
            {last ? `Last dream ${last}` : "No dream yet"}{next ? ` · next due ${next}` : ""}
          </p>
          {summary ? <p className="mt-0.5 text-[13px] text-muted-foreground">{summary}</p> : null}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <label htmlFor="dream-frequency" className="text-[13px] text-muted-foreground">Every</label>
          <select id="dream-frequency" value={interval} disabled={busy} onChange={(event) => onInterval(Number(event.target.value))} className="h-10 rounded-xl border border-input bg-card px-3 text-sm">
            {INTERVALS.map((item) => <option key={item.hours} value={item.hours}>{item.label}</option>)}
          </select>
          <Button disabled={busy} variant="outline" onClick={() => onSchedule(true)}>{settings.enabled ? "Save schedule" : "Enable dreaming"}</Button>
          {settings.enabled ? <Button disabled={busy} variant="ghost" onClick={() => onSchedule(false)}>Pause</Button> : null}
        </div>
      </div>
      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-4">
        <p className="min-w-0 flex-1 basis-64 text-xs leading-5 text-muted-foreground">
          Runs when due after five quiet minutes of server activity; active work may delay it. Eligible exact duplicates are merged automatically and originals stay recoverable. Uses exact content and shared words, with no model calls.
        </p>
        <div className="flex flex-wrap gap-2">
          <Button disabled={busy} variant="outline" onClick={onPreview}>Preview a run</Button>
          <Button disabled={busy} onClick={onRun}><RefreshCw className={busy ? "h-4 w-4 animate-spin" : "h-4 w-4"} aria-hidden="true" />{busy ? "Working…" : "Dream now"}</Button>
        </div>
      </div>
      {settings.last_error ? <p className="text-sm text-destructive" role="alert">{settings.last_error}</p> : null}
    </section>
  )
}
