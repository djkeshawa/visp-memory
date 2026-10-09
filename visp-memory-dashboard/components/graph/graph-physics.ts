import type { Scene } from "./graph-types"

const TARGET_LINK_LENGTH = 150
const SETTLED_ENERGY = 0.5

/**
 * Advances the force layout one step, in place: inverse-square repulsion between all nodes,
 * spring attraction along links, and a weak pull to the centre. Returns true once settled.
 */
export function stepLayout(scene: Scene, width: number, height: number, draggedId: string | null): boolean {
  let energy = 0
  for (const node of scene.nodes) {
    if (node.id === draggedId) continue
    for (const other of scene.nodes) {
      if (node === other) continue
      const dx = node.x - other.x
      const dy = node.y - other.y
      const distance = Math.max(Math.hypot(dx, dy), 1)
      const force = Math.min(2000 / (distance * distance), 2)
      node.vx += (dx / distance) * force
      node.vy += (dy / distance) * force
    }
    for (const neighborId of scene.adjacency.get(node.id) ?? []) {
      const other = scene.byId.get(neighborId)
      if (!other) continue
      const dx = other.x - node.x
      const dy = other.y - node.y
      const distance = Math.hypot(dx, dy) || 1
      const force = (distance - TARGET_LINK_LENGTH) * 0.01
      node.vx += (dx / distance) * force
      node.vy += (dy / distance) * force
    }
    node.vx += (width / 2 - node.x) * 0.0003
    node.vy += (height / 2 - node.y) * 0.0003
    node.vx *= 0.85
    node.vy *= 0.85
    node.x += node.vx
    node.y += node.vy
    energy += Math.abs(node.vx) + Math.abs(node.vy)
  }
  const settled = energy < SETTLED_ENERGY && draggedId === null
  if (settled) for (const node of scene.nodes) { node.vx = 0; node.vy = 0 }
  return settled
}
