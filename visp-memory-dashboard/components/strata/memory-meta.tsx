import { Check } from "lucide-react"
import { MEMORY_LAYER_CONFIG, normalizeMemoryLayer } from "@/lib/layers"
import { evidenceLabel, memoryTrustFlags, shortDate } from "@/lib/memory-trust"
import type { Memory } from "@/lib/types"
import { cn } from "@/lib/utils"
import { Pill } from "./primitives"

/**
 * The trust line under a memory: layer, category, date, review state, evidence, flags, id.
 * Shows only what the server reported.
 */
export function MemoryMeta({ memory, showId = true, className }: { memory: Memory; showId?: boolean; className?: string }) {
  const config = MEMORY_LAYER_CONFIG[normalizeMemoryLayer(memory.layer)]
  const date = shortDate(memory.createdAt)
  const evidence = evidenceLabel(memory)
  return (
    <span className={cn("flex flex-wrap items-center gap-x-3.5 gap-y-1 text-xs text-muted-foreground", className)}>
      <span className={cn("font-semibold", config.color)}>{config.label}</span>
      {memory.category ? <span>{memory.category}</span> : null}
      {date ? <time dateTime={memory.createdAt}>{date}</time> : null}
      {memory.approvedAt ? <span className="inline-flex items-center gap-1 text-success"><Check className="h-3 w-3" aria-hidden="true" />Reviewed</span> : null}
      {evidence ? <span>{evidence}</span> : null}
      {memoryTrustFlags(memory).map((flag) => <Pill key={flag.label} tone={flag.tone} className="py-0">{flag.label}</Pill>)}
      {showId ? <span className="max-w-[14ch] truncate font-mono" title={memory.id}>{memory.id}</span> : null}
    </span>
  )
}
