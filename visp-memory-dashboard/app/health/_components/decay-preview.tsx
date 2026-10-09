import { Pill } from "@/components/strata/primitives"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import type { TrustTone } from "@/lib/memory-trust"
import type { DecayPreviewItem, DecayPreviewResponse } from "@/lib/types"
import { percent } from "./health-stats"

const RISK: Record<DecayPreviewItem["risk"], { label: string; tone: TrustTone }> = {
  likely_to_decay: { label: "Likely to decay", tone: "danger" },
  weakening: { label: "Weakening", tone: "warning" },
  at_floor: { label: "At floor", tone: "neutral" },
  stable: { label: "Stable", tone: "success" },
}

function formatDays(value: number): string {
  return value >= 365 ? `${(value / 365).toFixed(1)}y` : `${Math.round(value)}d`
}

const clampWidth = (value: number) => `${Math.max(2, Math.min(100, value * 100))}%`

interface DecayPreviewProps {
  preview: DecayPreviewResponse | null
  halflifeDays: number
  limit: number
  isLoading: boolean
  onHalflife: (value: number) => void
  onLimit: (value: number) => void
  onPreview: () => void
}

export function DecayPreview({ preview, halflifeDays, limit, isLoading, onHalflife, onLimit, onPreview }: DecayPreviewProps) {
  const candidates = preview?.candidates ?? []
  return (
    <section aria-labelledby="decay-heading" className="surface overflow-hidden rounded-2xl">
      <div className="flex flex-wrap items-end justify-between gap-4 px-5 py-5 sm:px-6">
        <div className="flex min-w-0 flex-col gap-1">
          <h2 id="decay-heading" className="text-[15px] font-semibold">Decay preview</h2>
          <p className="text-[13px] text-muted-foreground">
            Preview only. Nothing changes until decay runs, and archived memories stay recoverable. Half-life {preview?.halflifeDays ?? halflifeDays}d, floor {percent(preview?.minImportance ?? 0.1)}.
          </p>
        </div>
        <div className="flex flex-wrap items-end gap-2.5">
          {preview ? <Pill tone={preview.decayEnabled ? "success" : "neutral"} className="mb-2.5">{preview.decayEnabled ? "Decay enabled" : "Decay disabled"}</Pill> : null}
          <div className="grid gap-1">
            <Label htmlFor="halflife" className="text-xs font-normal text-muted-foreground">Half-life days</Label>
            <Input id="halflife" type="number" min={1} max={3650} value={halflifeDays} onChange={(event) => onHalflife(Number(event.target.value) || 30)} className="h-10 w-24" />
          </div>
          <div className="grid gap-1">
            <Label htmlFor="limit" className="text-xs font-normal text-muted-foreground">Limit</Label>
            <Input id="limit" type="number" min={1} max={100} value={limit} onChange={(event) => onLimit(Number(event.target.value) || 25)} className="h-10 w-20" />
          </div>
          <Button onClick={onPreview} disabled={isLoading} className="h-10 font-semibold">Preview</Button>
        </div>
      </div>

      {isLoading ? (
        <p className="border-t border-border p-6 text-sm text-muted-foreground">Loading memory health...</p>
      ) : candidates.length ? (
        <div className="overflow-x-auto border-t border-border">
          <table className="w-full min-w-[42rem] border-collapse text-left text-sm">
            <thead>
              <tr className="text-xs text-muted-foreground">
                <th scope="col" className="px-5 py-2.5 font-medium sm:px-6">Memory</th>
                <th scope="col" className="px-3 py-2.5 font-medium">Layer</th>
                <th scope="col" className="px-3 py-2.5 font-medium">Importance now → projected</th>
                <th scope="col" className="px-5 py-2.5 text-right font-medium sm:px-6">Change</th>
              </tr>
            </thead>
            <tbody>
              {candidates.map((item) => <DecayRow key={item.memoryId} item={item} />)}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="border-t border-border p-6 text-sm text-muted-foreground">No active memories found.</p>
      )}
    </section>
  )
}

function DecayRow({ item }: { item: DecayPreviewItem }) {
  const risk = RISK[item.risk]
  return (
    <tr className="border-t border-border align-top">
      <td className="max-w-md px-5 py-3 sm:px-6">
        <span className="line-clamp-2 block">{item.snippet}</span>
        <span className="mt-0.5 block font-mono text-xs text-muted-foreground">{item.memoryId}</span>
        <span className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
          <Pill tone={risk.tone}>{risk.label}</Pill>
          <span>idle {formatDays(item.ageDays)}</span>
          <span>{item.accessCount} accesses</span>
          {item.category ? <span>{item.category}</span> : null}
        </span>
        <span className="mt-1 block text-xs text-muted-foreground">{item.reason}</span>
      </td>
      <td className="px-3 py-3 text-[13px] capitalize">{item.layer}</td>
      <td className="px-3 py-3">
        <div className="flex items-center gap-2.5">
          <div role="img" aria-label={`Importance ${percent(item.currentImportance)} now, ${percent(item.projectedImportance)} projected`} className="relative h-1.5 w-32 shrink-0 rounded-sm bg-border">
            <div className="absolute inset-y-0 left-0 rounded-sm bg-muted-foreground/60" style={{ width: clampWidth(item.currentImportance) }} />
            <div className="absolute inset-y-0 left-0 rounded-sm bg-warning" style={{ width: clampWidth(item.projectedImportance) }} />
          </div>
          <span className="whitespace-nowrap font-mono text-xs text-muted-foreground">{percent(item.currentImportance)} → {percent(item.projectedImportance)}</span>
        </div>
      </td>
      <td className="whitespace-nowrap px-5 py-3 text-right font-mono text-[13px] text-warning sm:px-6">−{percent(item.decayAmount)}</td>
    </tr>
  )
}
