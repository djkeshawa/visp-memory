import Link from "next/link"
import { MEMORY_LAYER_CONFIG, MEMORY_LAYERS } from "@/lib/layers"
import { projectHref } from "@/lib/project-selection"
import type { MemoryLayer, Stats } from "@/lib/types"
import { cn } from "@/lib/utils"

interface MemoryTotalsProps {
  stats: Stats | null
  repoId: string | null
  isLoading: boolean
  unavailable: boolean
}

const LAYER_DETAIL: Record<MemoryLayer, { hint: string; href: string }> = {
  raw: { hint: "Captures kept for reference · never recalled", href: "/memories" },
  episodic: { hint: "Decisions, events and lessons", href: "/memories" },
  semantic: { hint: "Facts that carry forward", href: "/graph" },
  intent: { hint: "Goals and planned work", href: "/intents" },
}

/** Server-reported counts, drawn as strata: how memory settles from raw captures into intent. */
export function MemoryTotals({ stats, repoId, isLoading, unavailable }: MemoryTotalsProps) {
  const ready = !isLoading && !unavailable && !!stats
  const counts = MEMORY_LAYERS.map((layer) => ({ layer, value: stats?.byLayer?.[layer] }))
  const layered = counts.reduce((sum, item) => sum + (item.value || 0), 0)
  const summary = !ready || !stats ? (isLoading ? "Loading…" : "Unavailable")
    : `${stats.totalMemories.toLocaleString()} memories · ${stats.connections.toLocaleString()} connections · ${stats.activeIntents.toLocaleString()} active intents`

  return (
    <section aria-label="Memory totals" aria-busy={isLoading} className="surface flex flex-col gap-5 rounded-2xl p-5 sm:p-6">
      <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
        <h2 className="text-[15px] font-semibold">Memory strata</h2>
        <span className="text-[13px] text-muted-foreground">{summary}</span>
      </div>
      {ready && layered > 0 ? (
        <div role="img" aria-label={counts.map((item) => `${MEMORY_LAYER_CONFIG[item.layer].label} ${item.value || 0}`).join(", ")} className="flex h-3.5 gap-[3px]">
          {counts.filter((item) => item.value).map((item) => (
            <span key={item.layer} className={cn("rounded", MEMORY_LAYER_CONFIG[item.layer].solid)} style={{ flex: `${item.value} 1 0` }} />
          ))}
        </div>
      ) : (
        <div aria-hidden="true" className="h-3.5 rounded bg-secondary" />
      )}
      <div className="grid grid-cols-[repeat(auto-fit,minmax(160px,1fr))] gap-4">
        {counts.map((item) => {
          const config = MEMORY_LAYER_CONFIG[item.layer]
          return (
            <Link key={item.layer} href={projectHref(LAYER_DETAIL[item.layer].href, repoId)} className="group flex min-w-0 flex-col gap-1 rounded-lg">
              <span className="flex items-center gap-2 text-[13px] text-muted-foreground">
                <span aria-hidden="true" className={cn("h-2.5 w-2.5 shrink-0 rounded-[3px]", config.solid)} />
                {config.label}
              </span>
              <span className="text-[28px] font-semibold leading-tight tracking-tight tabular-nums group-hover:text-highlight">
                {ready ? (item.value ?? 0).toLocaleString() : "—"}
              </span>
              <span className="text-xs text-muted-foreground">{LAYER_DETAIL[item.layer].hint}</span>
            </Link>
          )
        })}
      </div>
    </section>
  )
}
