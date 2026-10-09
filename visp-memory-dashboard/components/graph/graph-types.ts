import type { GraphLink, GraphNode, MemoryLayer, RelationshipConfidence, RelationshipEvidence } from "@/lib/types"

/** A node with the position and velocity of the force layout. Lives in a ref, never in React state. */
export interface SimNode {
  id: string
  label: string
  layer: MemoryLayer
  description?: string
  connections: number
  x: number
  y: number
  vx: number
  vy: number
}

export interface SimEdge {
  source: string
  target: string
  strength?: number
  label?: string
  evidence?: RelationshipEvidence | null
}

export interface Particle {
  progress: number
  speed: number
}

/** Everything the layout and the drawing code need, rebuilt whenever new graph data arrives. */
export interface Scene {
  nodes: SimNode[]
  edges: SimEdge[]
  byId: Map<string, SimNode>
  adjacency: Map<string, Set<string>>
  particles: Map<number, Particle[]>
}

export const EMPTY_SCENE: Scene = { nodes: [], edges: [], byId: new Map(), adjacency: new Map(), particles: new Map() }

export function buildScene(nodes: GraphNode[], links: GraphLink[], width: number, height: number): Scene {
  const counts = new Map<string, number>()
  for (const link of links) {
    counts.set(link.source, (counts.get(link.source) ?? 0) + 1)
    counts.set(link.target, (counts.get(link.target) ?? 0) + 1)
  }
  const simNodes: SimNode[] = nodes.map((node) => ({
    id: node.id,
    label: node.label,
    layer: node.layer,
    description: node.full_label,
    connections: counts.get(node.id) ?? 0,
    // Start near the middle so the layout settles quickly. The API returns no positions.
    x: width * (0.25 + Math.random() * 0.5),
    y: height * (0.25 + Math.random() * 0.5),
    vx: 0,
    vy: 0,
  }))
  const edges: SimEdge[] = links.map((link) => ({
    source: link.source, target: link.target, strength: link.value, label: link.label, evidence: link.evidence,
  }))
  const adjacency = new Map<string, Set<string>>(simNodes.map((node) => [node.id, new Set<string>()]))
  for (const edge of edges) {
    adjacency.get(edge.source)?.add(edge.target)
    adjacency.get(edge.target)?.add(edge.source)
  }
  // Particles are indexed by edge so highlighted drawing never filters the full list per frame.
  const particles = new Map<number, Particle[]>(
    edges.map((_, index) => [index, Array.from({ length: 3 }, () => ({ progress: Math.random(), speed: 0.002 + Math.random() * 0.003 }))]),
  )
  return { nodes: simNodes, edges, byId: new Map(simNodes.map((node) => [node.id, node])), adjacency, particles }
}

/** Nodes that match the search text and an active layer. */
export function visibleNodeIds(nodes: GraphNode[], query: string, activeLayers: MemoryLayer[]): Set<string> {
  const needle = query.trim().toLowerCase()
  return new Set(
    nodes
      .filter((node) => activeLayers.includes(node.layer))
      .filter((node) => !needle || node.label.toLowerCase().includes(needle) || node.full_label?.toLowerCase().includes(needle))
      .map((node) => node.id),
  )
}

export type LinkKind = RelationshipConfidence

export const LINK_KINDS: readonly LinkKind[] = ["observed", "inferred", "ambiguous", "manual"]

/** How a link was established. A link with no recorded evidence is treated as ambiguous, never as observed. */
export function linkKind(evidence?: RelationshipEvidence | null): LinkKind {
  const value = evidence?.confidence
  return value && LINK_KINDS.includes(value) ? value : "ambiguous"
}

export const LINK_KIND_LABEL: Record<LinkKind, string> = {
  observed: "Observed", inferred: "Inferred", ambiguous: "Ambiguous", manual: "Manual",
}

/** Dash pattern in px for each kind: solid, dashed, dotted. Manual links are solid and use the accent colour. */
export const LINK_DASH: Record<LinkKind, number[]> = { observed: [], inferred: [6, 4], ambiguous: [1, 5], manual: [] }
