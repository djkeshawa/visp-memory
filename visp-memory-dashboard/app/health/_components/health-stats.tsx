import { Panel, Stat } from "@/components/strata/primitives"
import type { DecayPreviewItem } from "@/lib/types"

export function percent(value: number): string {
  return `${Math.round(value * 100)}%`
}

function summarise(candidates: DecayPreviewItem[]) {
  const count = (risk: DecayPreviewItem["risk"]) => candidates.filter((item) => item.risk === risk).length
  const averageStrength = candidates.length > 0 ? candidates.reduce((total, item) => total + item.currentImportance, 0) / candidates.length : 0
  return { averageStrength, weakening: count("weakening"), likely: count("likely_to_decay"), atFloor: count("at_floor") }
}

/** Health numbers computed from the previewed memories only, not the whole store. */
export function HealthStats({ candidates }: { candidates: DecayPreviewItem[] }) {
  const { averageStrength, weakening, likely, atFloor } = summarise(candidates)
  const cells: { label: string; value: string | number; tone?: string }[] = [
    { label: "Average strength", value: percent(averageStrength) },
    { label: "Weakening", value: weakening },
    { label: "Likely to decay", value: likely, tone: likely > 0 ? "text-warning" : undefined },
    { label: "At floor", value: atFloor },
  ]
  return (
    <Panel title="Memory health" titleId="health-heading" aside={<span className="text-xs text-muted-foreground">Strength, decay risk and lifespan · across the {candidates.length} previewed memories</span>} className="overflow-hidden" bodyClassName="p-0 sm:px-0">
      <div className="grid grid-cols-[repeat(auto-fit,minmax(10.5rem,1fr))] gap-px border-t border-border bg-border">
        {cells.map((cell) => (
          <div key={cell.label} className="bg-card">
            <div className="px-5 py-4 sm:px-6">
              <Stat label={cell.label} value={cell.value} valueClassName={cell.tone} />
            </div>
          </div>
        ))}
      </div>
    </Panel>
  )
}
