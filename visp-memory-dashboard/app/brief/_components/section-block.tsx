import type { TaskBriefContradiction, TaskBriefItem, TaskBriefSectionName } from "@/lib/types"
import { cn } from "@/lib/utils"
import { SECTION_STYLE } from "./sections"

const score = (value?: number) => (typeof value === "number" ? value.toFixed(3) : "0.000")

function Item({ item, boxed = false }: { item: TaskBriefItem; boxed?: boolean }) {
  const where = [...item.files, ...item.symbols]
  return (
    <li className={cn("flex flex-col gap-1", boxed && "rounded-xl bg-destructive/12 p-3.5")}>
      <p className="text-sm leading-6">{item.content}</p>
      <p className="flex flex-wrap gap-x-3.5 gap-y-0.5 text-xs text-muted-foreground">
        <span className="font-mono">[{item.citation}]</span>
        {item.retrievalChannels.length ? <span>{item.retrievalChannels.join(" · ")}</span> : null}
        {typeof item.confidence === "number" ? <span>confidence {item.confidence.toFixed(2)}</span> : null}
        {where.length ? <span className="break-all font-mono">{where.join(" | ")}</span> : null}
      </p>
      {item.retrievalChannels.length ? (
        <p className="text-xs tabular-nums text-muted-foreground">
          Ranking: direct {score(item.retrievalFactors.directScore)} | graph {score(item.retrievalFactors.graphScore)} | fusion {score(item.retrievalFactors.rrfScore)}
        </p>
      ) : null}
    </li>
  )
}

function Contradictions({ items }: { items: TaskBriefContradiction[] }) {
  return (
    <div role="group" aria-label="Contradictions" className="overflow-hidden rounded-xl border border-intent/35 bg-intent/5">
      <p className="border-b border-intent/20 px-4 py-2.5 text-sm font-semibold">Contradictions</p>
      <ul className="divide-y divide-intent/20">
        {items.map((item) => (
          <li key={`${item.citation}:${item.otherMemoryId}`} className="px-4 py-3 text-sm">
            <span className="font-mono font-medium">[{item.citation}]</span>{" "}
            <span className="text-muted-foreground">conflicts with <span className="font-mono">{item.otherMemoryId}</span>: {item.otherSnippet}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

/** One section of the brief. Warnings also carry the contradictions the server found. */
export function SectionBlock({ name, items, contradictions = [] }: { name: TaskBriefSectionName; items: TaskBriefItem[]; contradictions?: TaskBriefContradiction[] }) {
  if (!items.length && !contradictions.length) return null
  const style = SECTION_STYLE[name]
  const warning = name === "warnings"
  return (
    <section aria-labelledby={`brief-${name}`} className="flex flex-col gap-3 border-b border-border px-5 py-4 sm:px-6">
      <h3 id={`brief-${name}`} className={cn("eyebrow", style.text)}>{style.label}</h3>
      {contradictions.length ? <Contradictions items={contradictions} /> : null}
      <ul className="flex flex-col gap-3.5">
        {items.map((item) => <Item key={item.citation} item={item} boxed={warning} />)}
      </ul>
    </section>
  )
}
