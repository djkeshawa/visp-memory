import { MEMORY_LAYER_CONFIG, MEMORY_LAYERS } from "@/lib/layers"
import type { MemoryLayer } from "@/lib/types"
import { LINK_DASH, linkKind, type Scene } from "./graph-types"

export interface GraphTheme {
  foreground: string
  muted: string
  dim: string
  accent: string
  font: string
  layers: Record<MemoryLayer, string>
}

let cached: { key: string; theme: GraphTheme } | null = null

/**
 * Reads the design tokens the canvas needs. A canvas cannot use CSS variables directly, so
 * the colours are resolved per theme (light or dark) and cached until the theme class changes.
 */
export function readGraphTheme(): GraphTheme {
  const root = document.documentElement
  const key = `${root.className}|${root.dataset.theme ?? ""}`
  if (cached?.key === key) return cached.theme
  const style = getComputedStyle(root)
  const token = (name: string, fallback: string) => style.getPropertyValue(name).trim() || fallback
  const layers = Object.fromEntries(
    MEMORY_LAYERS.map((layer) => [layer, token(`--${layer}`, MEMORY_LAYER_CONFIG[layer].fill)]),
  ) as Record<MemoryLayer, string>
  const theme: GraphTheme = {
    foreground: token("--foreground", "#eceef1"),
    muted: token("--muted-foreground", "#a3aab4"),
    dim: token("--input", "#2c3139"),
    accent: token("--highlight", "#8ab4ff"),
    font: getComputedStyle(document.body).fontFamily || "system-ui, sans-serif",
    layers,
  }
  cached = { key, theme }
  return theme
}

export interface DrawState {
  scene: Scene
  visible: Set<string>
  hoveredId: string | null
  selectedId: string | null
  zoom: number
  panX: number
  panY: number
  /** Device pixel ratio, so lines stay crisp on dense screens. */
  scale: number
  reducedMotion: boolean
}

const LABEL_LIMIT = 28
/** Above this many nodes, labels appear only for the focused node, its neighbours, or when zoomed in. */
const LABEL_ALL_NODES = 40

function nodeRadius(connections: number, emphasised: boolean) {
  return 7 + Math.min(connections, 6) * 0.6 + (emphasised ? 3 : 0)
}

/** Draws edges (style shows how each link was established), then nodes and labels. */
export function drawGraph(ctx: CanvasRenderingContext2D, width: number, height: number, state: DrawState, theme: GraphTheme) {
  const { scene, visible, hoveredId, selectedId, zoom, panX, panY, scale, reducedMotion } = state
  ctx.setTransform(1, 0, 0, 1, 0, 0)
  ctx.clearRect(0, 0, width, height)
  ctx.setTransform(scale * zoom, 0, 0, scale * zoom, scale * panX, scale * panY)
  const focusId = hoveredId ?? selectedId

  scene.edges.forEach((edge, index) => {
    const from = scene.byId.get(edge.source)
    const to = scene.byId.get(edge.target)
    if (!from || !to) return
    const fromVisible = visible.has(from.id)
    const toVisible = visible.has(to.id)
    if (!fromVisible && !toVisible) return
    const kind = linkKind(edge.evidence)
    const active = hoveredId === edge.source || hoveredId === edge.target || selectedId === edge.source || selectedId === edge.target
    const bothVisible = fromVisible && toVisible
    ctx.globalAlpha = !bothVisible ? 0.12 : active ? 1 : 0.55
    ctx.strokeStyle = kind === "manual" ? theme.accent : active ? theme.foreground : theme.muted
    ctx.lineWidth = (1 + (edge.strength ?? 0.5) * 1.2) / zoom
    ctx.lineCap = kind === "ambiguous" ? "round" : "butt"
    ctx.setLineDash(LINK_DASH[kind].map((value) => value / zoom))
    ctx.beginPath()
    ctx.moveTo(from.x, from.y)
    ctx.lineTo(to.x, to.y)
    ctx.stroke()
    ctx.setLineDash([])
    if (active && bothVisible && !reducedMotion) {
      ctx.fillStyle = theme.foreground
      for (const particle of scene.particles.get(index) ?? []) {
        particle.progress = (particle.progress + particle.speed) % 1
        ctx.beginPath()
        ctx.arc(from.x + (to.x - from.x) * particle.progress, from.y + (to.y - from.y) * particle.progress, 2 / zoom, 0, Math.PI * 2)
        ctx.fill()
      }
    }
  })
  ctx.lineCap = "butt"

  for (const node of scene.nodes) {
    const shown = visible.has(node.id)
    const selected = selectedId === node.id
    const hovered = hoveredId === node.id
    const connected = focusId !== null && scene.adjacency.get(focusId)?.has(node.id) === true
    const emphasised = shown && (selected || hovered)
    const radius = nodeRadius(node.connections, emphasised)
    const color = theme.layers[node.layer]
    ctx.globalAlpha = shown ? 1 : 0.2
    if (selected && shown) {
      ctx.globalAlpha = 0.14
      ctx.fillStyle = color
      ctx.beginPath()
      ctx.arc(node.x, node.y, radius * 2, 0, Math.PI * 2)
      ctx.fill()
      ctx.globalAlpha = 1
    }
    ctx.fillStyle = color
    ctx.beginPath()
    ctx.arc(node.x, node.y, radius, 0, Math.PI * 2)
    ctx.fill()
    if (selected && shown) {
      ctx.strokeStyle = theme.foreground
      ctx.lineWidth = 2.5 / zoom
      ctx.beginPath()
      ctx.arc(node.x, node.y, radius + 2 / zoom, 0, Math.PI * 2)
      ctx.stroke()
    }
    if (shown && (emphasised || connected || zoom >= 1.5 || (zoom > 0.8 && scene.nodes.length <= LABEL_ALL_NODES))) {
      const text = node.label.length > LABEL_LIMIT ? `${node.label.slice(0, LABEL_LIMIT - 1)}…` : node.label
      ctx.globalAlpha = 1
      ctx.font = `${emphasised ? 600 : 400} ${13 / zoom}px ${theme.font}`
      ctx.textAlign = "center"
      ctx.fillStyle = emphasised || connected ? theme.foreground : theme.muted
      ctx.fillText(text, node.x, node.y - radius - 8 / zoom)
    }
  }
  ctx.globalAlpha = 1
}
