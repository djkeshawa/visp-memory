import { STRATA_ORDER, normalizeMemoryLayer } from "@/lib/layers"
import type { MemoryLayer } from "@/lib/types"
import { cn } from "@/lib/utils"

const STROKE: Record<MemoryLayer, string> = {
  raw: "stroke-raw",
  episodic: "stroke-episodic",
  semantic: "stroke-semantic",
  intent: "stroke-intent",
}

/** Four stacked strata with the memory's own layer lit. Decorative: pair it with a text label. */
export function LayerGlyph({ layer, className }: { layer: unknown; className?: string }) {
  const active = normalizeMemoryLayer(layer)
  return (
    <svg viewBox="0 0 16 16" aria-hidden="true" className={cn("h-4 w-4 shrink-0", className)}>
      {STRATA_ORDER.map((item, index) => (
        <path
          key={item}
          d={`M2 ${3 + index * 3.5}h12`}
          strokeWidth={2}
          strokeLinecap="round"
          className={item === active ? STROKE[item] : "stroke-input"}
        />
      ))}
    </svg>
  )
}

/** The product mark: uneven strata on a solid tile. */
export function StrataMark({ className }: { className?: string }) {
  return (
    <span className={cn("flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-primary", className)}>
      <svg viewBox="0 0 16 16" aria-hidden="true" className="h-4 w-4">
        {[12, 9, 12, 6].map((width, index) => (
          <path key={index} d={`M2 ${3 + index * 3.5}h${width}`} strokeWidth={2} strokeLinecap="round" className="stroke-primary-foreground" />
        ))}
      </svg>
    </span>
  )
}
