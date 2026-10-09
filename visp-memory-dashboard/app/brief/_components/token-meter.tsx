import type { TaskMemoryBrief } from "@/lib/types"
import { SECTION_ORDER, SECTION_STYLE } from "./sections"

/**
 * Tokens used of the budget. The server reports the total, not tokens per section, so the used
 * share is divided by how many items each section holds, and the caption says so.
 */
export function TokenMeter({ brief }: { brief: TaskMemoryBrief }) {
  const used = brief.tokenBudget > 0 ? Math.min(100, (brief.tokenCount / brief.tokenBudget) * 100) : 0
  const counts = SECTION_ORDER.map((name) => ({ name, count: brief.sections[name]?.length ?? 0 })).filter((item) => item.count > 0)
  const total = counts.reduce((sum, item) => sum + item.count, 0)
  const label = `${brief.tokenCount.toLocaleString()} of ${brief.tokenBudget.toLocaleString()} tokens used` +
    (counts.length ? `. Items: ${counts.map((item) => `${SECTION_STYLE[item.name].label.toLowerCase()} ${item.count}`).join(", ")}` : "")
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 text-[13px]">
        <span>
          <span className="font-mono font-medium tabular-nums">{brief.tokenCount.toLocaleString()}</span>{" "}
          <span className="text-muted-foreground">of {brief.tokenBudget.toLocaleString()} tokens · {brief.metrics.selectedCount} of {brief.metrics.candidateCount} candidates selected</span>
        </span>
        <span className="max-w-full truncate font-mono text-xs text-muted-foreground" title={brief.fingerprint}>fp {brief.fingerprint}</span>
      </div>
      <div role="img" aria-label={label} className="flex h-2.5 gap-0.5">
        {counts.length ? counts.map((item) => (
          <span key={item.name} className={`rounded-sm ${SECTION_STYLE[item.name].bar}`} style={{ flex: `${(used * item.count) / total} 1 0` }} />
        )) : <span className="rounded-sm bg-muted-foreground" style={{ flex: `${used} 1 0` }} />}
        <span className="rounded-sm bg-border" style={{ flex: `${100 - used} 1 0` }} />
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
        {counts.map((item) => (
          <span key={item.name} className="inline-flex items-center gap-1.5">
            <span aria-hidden="true" className={`h-2 w-2 rounded-sm ${SECTION_STYLE[item.name].bar}`} />{SECTION_STYLE[item.name].label} {item.count}
          </span>
        ))}
      </div>
      <p className="text-xs text-muted-foreground">Segments split the used share by item count; per-section token counts are not reported.</p>
    </div>
  )
}
