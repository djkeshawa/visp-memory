"use client"

import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react"
import { drawGraph, readGraphTheme } from "./graph-draw"
import { stepLayout } from "./graph-physics"
import { EMPTY_SCENE, buildScene, visibleNodeIds, type Scene } from "./graph-types"
import type { GraphData, MemoryLayer } from "@/lib/types"

interface MemoryGraphProps {
  graph: GraphData | null
  loading: boolean
  query: string
  activeLayers: MemoryLayer[]
  selectedId: string | null
  onSelect: (id: string | null) => void
}

const MIN_ZOOM = 0.3
const MAX_ZOOM = 3
const CLICK_SLOP = 4
const CONTROL = "flex h-10 min-w-10 items-center justify-center rounded-[10px] border border-border bg-card px-2 text-sm font-medium text-foreground hover:bg-secondary"

/**
 * The force-directed graph canvas. Node positions live in a ref and are drawn straight to the
 * canvas each frame, so layout never re-renders React. Selection is owned by the page.
 */
export function MemoryGraph({ graph, loading, query, activeLayers, selectedId, onSelect }: MemoryGraphProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const [size, setSize] = useState({ width: 800, height: 560, dpr: 1 })
  const [isSettled, setIsSettled] = useState(false)

  const sceneRef = useRef<Scene>(EMPTY_SCENE)
  const sizeRef = useRef(size)
  const viewRef = useRef({ zoom: 1, x: 0, y: 0 })
  const hoveredRef = useRef<string | null>(null)
  const draggedRef = useRef<string | null>(null)
  const panningRef = useRef(false)
  const pressRef = useRef<{ id: string | null; x: number; y: number; moved: boolean } | null>(null)
  const lastPointRef = useRef({ x: 0, y: 0 })
  const settledRef = useRef(false)
  const frameRef = useRef<number | null>(null)
  const reducedMotionRef = useRef(false)
  const renderRef = useRef<() => void>(() => undefined)

  const visible = useMemo(() => (graph ? visibleNodeIds(graph.nodes, query, activeLayers) : new Set<string>()), [graph, query, activeLayers])
  const visibleRef = useRef(visible)
  const selectedRef = useRef(selectedId)
  const nodeCount = graph?.nodes.length ?? 0

  const schedule = useCallback(() => {
    if (frameRef.current !== null) return
    frameRef.current = requestAnimationFrame(() => {
      frameRef.current = null
      renderRef.current()
    })
  }, [])

  const restartLayout = useCallback(() => {
    settledRef.current = false
    setIsSettled(false)
    schedule()
  }, [schedule])

  useEffect(() => {
    renderRef.current = () => {
      const canvas = canvasRef.current
      const ctx = canvas?.getContext("2d")
      if (!canvas || !ctx) return
      const { width, height, dpr } = sizeRef.current
      if (!settledRef.current || draggedRef.current) {
        if (stepLayout(sceneRef.current, width, height, draggedRef.current)) {
          settledRef.current = true
          setIsSettled(true)
        }
      }
      drawGraph(ctx, canvas.width, canvas.height, {
        scene: sceneRef.current,
        visible: visibleRef.current,
        hoveredId: hoveredRef.current,
        selectedId: selectedRef.current,
        zoom: viewRef.current.zoom,
        panX: viewRef.current.x,
        panY: viewRef.current.y,
        scale: dpr,
        reducedMotion: reducedMotionRef.current,
      }, readGraphTheme())
      const animating = !reducedMotionRef.current && (hoveredRef.current !== null || selectedRef.current !== null)
      if (!settledRef.current || draggedRef.current || animating) schedule()
    }
  })

  // New graph data (or a cleared graph while the next project loads) replaces the scene.
  useEffect(() => {
    sceneRef.current = graph ? buildScene(graph.nodes, graph.links, sizeRef.current.width, sizeRef.current.height) : EMPTY_SCENE
    hoveredRef.current = null
    draggedRef.current = null
    panningRef.current = false
    pressRef.current = null
    restartLayout()
  }, [graph, restartLayout])

  // Filters and resizing are the only other events that restart the layout.
  useEffect(() => {
    visibleRef.current = visible
    sizeRef.current = size
    restartLayout()
  }, [visible, size, restartLayout])

  useEffect(() => {
    selectedRef.current = selectedId
    schedule()
  }, [selectedId, schedule])

  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    reducedMotionRef.current = window.matchMedia("(prefers-reduced-motion: reduce)").matches
    const observer = new ResizeObserver(([entry]) => {
      if (entry) setSize({ width: entry.contentRect.width, height: entry.contentRect.height, dpr: window.devicePixelRatio || 1 })
    })
    observer.observe(container)
    return () => {
      observer.disconnect()
      if (frameRef.current !== null) cancelAnimationFrame(frameRef.current)
      frameRef.current = null
    }
  }, [])

  const zoomAt = useCallback((factor: number, px: number, py: number) => {
    const view = viewRef.current
    const zoom = Math.min(Math.max(view.zoom * factor, MIN_ZOOM), MAX_ZOOM)
    const ratio = zoom / view.zoom
    view.x = px - (px - view.x) * ratio
    view.y = py - (py - view.y) * ratio
    view.zoom = zoom
    schedule()
  }, [schedule])

  // React attaches wheel listeners as passive, which cannot stop the page from scrolling.
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const onWheel = (event: WheelEvent) => {
      event.preventDefault()
      const rect = canvas.getBoundingClientRect()
      zoomAt(event.deltaY > 0 ? 0.9 : 1.1, event.clientX - rect.left, event.clientY - rect.top)
    }
    canvas.addEventListener("wheel", onWheel, { passive: false })
    return () => canvas.removeEventListener("wheel", onWheel)
  }, [zoomAt])

  const localPoint = (event: ReactPointerEvent<HTMLCanvasElement>) => {
    const rect = event.currentTarget.getBoundingClientRect()
    return { x: event.clientX - rect.left, y: event.clientY - rect.top }
  }

  const nodeAt = (x: number, y: number) => {
    const { zoom, x: panX, y: panY } = viewRef.current
    const cx = (x - panX) / zoom
    const cy = (y - panY) / zoom
    const reach = Math.min(Math.max(14 / zoom, 8), 40)
    let best: { id: string; distance: number } | null = null
    for (const node of sceneRef.current.nodes) {
      const distance = Math.hypot(cx - node.x, cy - node.y)
      if (distance < reach && (!best || distance < best.distance)) best = { id: node.id, distance }
    }
    return best?.id ?? null
  }

  const handleDown = (event: ReactPointerEvent<HTMLCanvasElement>) => {
    const point = localPoint(event)
    const id = nodeAt(point.x, point.y)
    pressRef.current = { id, ...point, moved: false }
    lastPointRef.current = point
    if (id) draggedRef.current = id
    else panningRef.current = true
    try { event.currentTarget.setPointerCapture(event.pointerId) } catch { /* capture is optional */ }
    if (id) restartLayout()
  }

  const handleMove = (event: ReactPointerEvent<HTMLCanvasElement>) => {
    const point = localPoint(event)
    const press = pressRef.current
    if (press && Math.hypot(point.x - press.x, point.y - press.y) > CLICK_SLOP) press.moved = true
    const view = viewRef.current
    if (panningRef.current) {
      view.x += point.x - lastPointRef.current.x
      view.y += point.y - lastPointRef.current.y
      lastPointRef.current = point
      schedule()
    } else if (draggedRef.current) {
      const node = sceneRef.current.byId.get(draggedRef.current)
      if (node && press?.moved) {
        node.x = (point.x - view.x) / view.zoom
        node.y = (point.y - view.y) / view.zoom
        node.vx = 0
        node.vy = 0
      }
      schedule()
    } else {
      const id = nodeAt(point.x, point.y)
      if (id !== hoveredRef.current) {
        hoveredRef.current = id
        event.currentTarget.style.cursor = id ? "pointer" : "grab"
        schedule()
      }
    }
  }

  const finishPress = (commit: boolean) => {
    const press = pressRef.current
    pressRef.current = null
    // A press that never moved is a click: on a node it selects, on empty space it clears.
    if (commit && press && !press.moved) onSelect(press.id)
    const wasDragging = draggedRef.current !== null
    draggedRef.current = null
    panningRef.current = false
    if (wasDragging) restartLayout()
    else schedule()
  }

  const handleLeave = () => {
    hoveredRef.current = null
    if (pressRef.current === null) schedule()
  }

  const zoomFromCentre = (factor: number) => zoomAt(factor, size.width / 2, size.height / 2)
  const fitView = () => { viewRef.current = { zoom: 1, x: 0, y: 0 }; schedule() }
  const nodes = graph?.nodes ?? []
  const shown = nodes.filter((node) => visible.has(node.id))

  return (
    <figure className="relative m-0 min-w-0 flex-[999_1_32rem] overflow-hidden rounded-2xl border border-border bg-background">
      <div ref={containerRef} className="relative h-[26rem] sm:h-[34rem] lg:h-[38rem]">
        <canvas
          ref={canvasRef}
          width={Math.max(1, Math.floor(size.width * size.dpr))}
          height={Math.max(1, Math.floor(size.height * size.dpr))}
          role="img"
          aria-label="Interactive memory graph"
          onPointerDown={handleDown}
          onPointerMove={handleMove}
          onPointerUp={() => finishPress(true)}
          onPointerCancel={() => finishPress(false)}
          onPointerLeave={handleLeave}
          className="h-full w-full cursor-grab"
        />

        <span className="sr-only" data-testid="graph-node-labels">{shown.map((node) => node.label).join(" | ")}</span>
        <span className="sr-only" data-testid="graph-link-count">
          {graph?.links.length ?? 0} graph link{graph?.links.length === 1 ? "" : "s"}
        </span>
        <div role="group" className="pointer-events-none absolute left-3 top-3 z-10 flex max-w-[calc(100%-1.5rem)] flex-wrap gap-1" data-testid="graph-node-controls" aria-label="Graph node controls">
          {shown.map((node) => (
            <button
              type="button"
              key={node.id}
              data-testid={`graph-node-control-${node.id}`}
              aria-label={`Select graph node ${node.label}`}
              onClick={() => onSelect(node.id)}
              className="sr-only focus-visible:not-sr-only focus-visible:pointer-events-auto focus-visible:rounded-lg focus-visible:bg-card focus-visible:px-3 focus-visible:py-1.5 focus-visible:text-sm focus-visible:shadow"
            >
              {node.label}
            </button>
          ))}
        </div>

        {loading || nodeCount === 0 || shown.length === 0 ? (
          <p className="pointer-events-none absolute inset-0 flex items-center justify-center px-6 text-center text-sm text-muted-foreground">
            {loading ? "Loading graph…" : nodeCount === 0 ? "No memories in this graph yet." : "No nodes match the current layers and search."}
          </p>
        ) : null}

        <div className="absolute right-3 top-3 flex flex-col gap-1.5">
          <button type="button" aria-label="Zoom in" onClick={() => zoomFromCentre(1.2)} className={CONTROL}>+</button>
          <button type="button" aria-label="Zoom out" onClick={() => zoomFromCentre(1 / 1.2)} className={CONTROL}>−</button>
          <button type="button" onClick={fitView} className={CONTROL}>Fit</button>
        </div>
      </div>
      <figcaption className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 border-t border-border px-4 py-2.5 text-xs text-muted-foreground">
        <span role="status" data-testid="graph-settlement">
          {isSettled ? "Layout settled" : "Arranging layout"} · {shown.length} of {nodeCount} nodes visible
        </span>
        <span>Scroll to zoom · drag to pan · click a node to inspect</span>
      </figcaption>
    </figure>
  )
}
