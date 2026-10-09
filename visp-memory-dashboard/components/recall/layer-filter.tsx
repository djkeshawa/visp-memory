import { MEMORY_LAYERS, MEMORY_LAYER_CONFIG } from "@/lib/layers"
import type { MemoryLayer } from "@/lib/types"
import { cn } from "@/lib/utils"

export type LayerChoice = MemoryLayer | "all"

/** Raw is deliberately absent: recall never shows raw captures. */
const CHOICES: LayerChoice[] = ["all", ...MEMORY_LAYERS.filter((layer) => MEMORY_LAYER_CONFIG[layer].recallable)]

export function LayerFilter({ value, onChange }: { value: LayerChoice; onChange: (value: LayerChoice) => void }) {
  return (
    <div role="group" aria-label="Filter by layer" className="flex flex-wrap gap-1.5">
      {CHOICES.map((choice) => (
        <button
          key={choice}
          type="button"
          aria-pressed={value === choice}
          onClick={() => onChange(choice)}
          className={cn(
            "h-10 rounded-full border px-4 text-[13px] transition-colors sm:h-9",
            value === choice ? "border-primary bg-primary font-semibold text-primary-foreground" : "border-input text-muted-foreground hover:bg-secondary hover:text-foreground",
          )}
        >
          {choice === "all" ? "All layers" : MEMORY_LAYER_CONFIG[choice].label}
        </button>
      ))}
    </div>
  )
}
