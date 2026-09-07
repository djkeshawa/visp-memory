import { Brain, Database, FileText, Target, type LucideIcon } from "lucide-react"
import type { MemoryLayer } from "./types"

export interface MemoryLayerConfig {
  label: string
  icon: LucideIcon
  color: string
  bgColor: string
  fill: string
  glow: string
  bg: string
  recallable: boolean
  graphVisibleByDefault: boolean
}

export const MEMORY_LAYERS = ["raw", "episodic", "semantic", "intent"] as const satisfies readonly MemoryLayer[]

export const MEMORY_LAYER_CONFIG: Record<MemoryLayer, MemoryLayerConfig> = {
  raw: {
    label: "Raw",
    icon: FileText,
    color: "text-muted-foreground",
    bgColor: "bg-secondary",
    fill: "#94a3b8",
    glow: "rgba(148, 163, 184, 0.55)",
    bg: "rgba(148, 163, 184, 0.12)",
    recallable: false,
    graphVisibleByDefault: false,
  },
  episodic: {
    label: "Episodic",
    icon: Database,
    color: "text-episodic",
    bgColor: "bg-episodic/10",
    fill: "#a78bfa",
    glow: "rgba(167, 139, 250, 0.6)",
    bg: "rgba(167, 139, 250, 0.1)",
    recallable: true,
    graphVisibleByDefault: true,
  },
  semantic: {
    label: "Semantic",
    icon: Brain,
    color: "text-semantic",
    bgColor: "bg-semantic/10",
    fill: "#22d3ee",
    glow: "rgba(34, 211, 238, 0.6)",
    bg: "rgba(34, 211, 238, 0.1)",
    recallable: true,
    graphVisibleByDefault: true,
  },
  intent: {
    label: "Intent",
    icon: Target,
    color: "text-intent",
    bgColor: "bg-intent/10",
    fill: "#fbbf24",
    glow: "rgba(251, 191, 36, 0.6)",
    bg: "rgba(251, 191, 36, 0.1)",
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
