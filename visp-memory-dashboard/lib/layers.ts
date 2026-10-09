import { Brain, Database, FileText, Target, type LucideIcon } from "lucide-react"
import type { MemoryLayer } from "./types"

export interface MemoryLayerConfig {
  label: string
  icon: LucideIcon
  /** Text colour class, e.g. "text-episodic". */
  color: string
  /** Solid background class for bars, dots and glyphs. */
  solid: string
  /** Canvas fallback when the CSS layer token cannot be read. */
  fill: string
  recallable: boolean
  graphVisibleByDefault: boolean
}

export const MEMORY_LAYERS = ["raw", "episodic", "semantic", "intent"] as const satisfies readonly MemoryLayer[]

/** Strata order, top to bottom, as drawn by LayerGlyph and the strata bar. */
export const STRATA_ORDER = ["intent", "semantic", "episodic", "raw"] as const satisfies readonly MemoryLayer[]

export const MEMORY_LAYER_CONFIG: Record<MemoryLayer, MemoryLayerConfig> = {
  raw: {
    label: "Raw",
    icon: FileText,
    color: "text-raw",
    solid: "bg-raw",
    fill: "#9aa1ab",
    recallable: false,
    graphVisibleByDefault: false,
  },
  episodic: {
    label: "Episodic",
    icon: Database,
    color: "text-episodic",
    solid: "bg-episodic",
    fill: "#b8a4ff",
    recallable: true,
    graphVisibleByDefault: true,
  },
  semantic: {
    label: "Semantic",
    icon: Brain,
    color: "text-semantic",
    solid: "bg-semantic",
    fill: "#63d3c2",
    recallable: true,
    graphVisibleByDefault: true,
  },
  intent: {
    label: "Intent",
    icon: Target,
    color: "text-intent",
    solid: "bg-intent",
    fill: "#f0b45e",
    recallable: true,
    graphVisibleByDefault: true,
  },
}

export const DEFAULT_GRAPH_LAYERS: MemoryLayer[] = MEMORY_LAYERS.filter(
  (layer) => MEMORY_LAYER_CONFIG[layer].graphVisibleByDefault,
)

export function isMemoryLayer(value: unknown): value is MemoryLayer {
  return typeof value === "string" && (MEMORY_LAYERS as readonly string[]).includes(value)
}

export function normalizeMemoryLayer(value: unknown): MemoryLayer {
  return isMemoryLayer(value) ? value : "episodic"
}

export function isRecallableLayer(value: unknown): boolean {
  return isMemoryLayer(value) && MEMORY_LAYER_CONFIG[value].recallable
}
