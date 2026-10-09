import { Search } from "lucide-react"
import { MEMORY_LAYERS, MEMORY_LAYER_CONFIG } from "@/lib/layers"
import type { MemoryLayer } from "@/lib/types"
import { cn } from "@/lib/utils"
import { LINK_DASH, LINK_KINDS, LINK_KIND_LABEL } from "./graph-types"

export function NodeSearch({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  return (
    <div className="flex h-11 min-w-0 flex-[0_1_18.75rem] items-center gap-2 rounded-[10px] border border-input bg-card px-3 focus-within:border-ring focus-within:ring-[3px] focus-within:ring-ring/30">
      <Search className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
      <label htmlFor="graph-find" className="sr-only">Find a node</label>
      <input
        id="graph-find"
        type="search"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder="Find a node"
        className="min-w-0 flex-1 bg-transparent text-base outline-none placeholder:text-muted-foreground md:text-sm"
      />
    </div>
  )
}

const RING: Record<MemoryLayer, string> = { raw: "border-raw", episodic: "border-episodic", semantic: "border-semantic", intent: "border-intent" }

interface LayerChipsProps {
  counts: Record<MemoryLayer, number>
  active: MemoryLayer[]
  onToggle: (layer: MemoryLayer) => void
}

/** One toggle per memory layer with the number of nodes it holds. Raw is off until switched on. */
export function LayerChips({ counts, active, onToggle }: LayerChipsProps) {
  return (
    <div role="group" aria-label="Visible layers" className="flex flex-wrap gap-1.5">
      {MEMORY_LAYERS.map((layer) => {
        const config = MEMORY_LAYER_CONFIG[layer]
        const on = active.includes(layer)
        return (
          <button
            key={layer}
            type="button"
            aria-pressed={on}
            data-testid={`graph-filter-${layer}`}
            onClick={() => onToggle(layer)}
            className={cn(
              "inline-flex h-10 items-center gap-2 rounded-full border px-3 text-[13px] transition-colors",
              on ? "border-input bg-secondary text-foreground" : "border-border text-muted-foreground hover:text-foreground",
            )}
          >
            <span aria-hidden="true" className={cn("h-2 w-2 rounded-full border-[1.5px]", RING[layer], on && config.solid)} />
            <span>{config.label}</span>
            <span className="tabular-nums text-muted-foreground">{counts[layer]}</span>
          </button>
        )
      })}
    </div>
  )
}

/** How to read a link: solid is observed, dashed inferred, dotted ambiguous, accent manual. */
export function ConfidenceLegend() {
  return (
    <ul aria-label="Link confidence" className="m-0 flex list-none flex-wrap gap-x-3.5 gap-y-1 p-0 text-xs text-muted-foreground">
      {LINK_KINDS.map((kind) => (
        <li key={kind} className="inline-flex items-center gap-1.5">
          <svg width="22" height="6" aria-hidden="true" className={kind === "manual" ? "text-highlight" : "text-muted-foreground"}>
            <path
              d="M1 3h20"
              stroke="currentColor"
              strokeWidth="2"
              strokeDasharray={LINK_DASH[kind].join(" ") || undefined}
              strokeLinecap={kind === "ambiguous" ? "round" : "butt"}
            />
          </svg>
          {LINK_KIND_LABEL[kind]}
        </li>
      ))}
    </ul>
  )
}
