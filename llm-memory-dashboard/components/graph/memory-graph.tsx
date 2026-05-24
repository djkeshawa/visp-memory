"use client"

import type React from "react"
import { useCallback, useEffect, useRef, useState } from "react"
import { motion, AnimatePresence } from "framer-motion"
import type { MemoryLayer } from "@/lib/types"
import { AlertTriangle, Search, ZoomIn, ZoomOut, Maximize2, Filter, X, Link2, Clock, Tag } from "lucide-react"
import { describeApiError, getGraphData } from "@/lib/api"

interface GraphNode {
  id: string
  label: string
  layer: MemoryLayer
  description?: string
  connections?: number
  createdAt?: string
  x: number
  y: number
  vx: number
  vy: number
}

interface GraphEdge {
  source: string
  target: string
  strength?: number
}

const layerColors: Record<MemoryLayer, { fill: string; glow: string; bg: string }> = {
  episodic: { fill: "#a78bfa", glow: "rgba(167, 139, 250, 0.6)", bg: "rgba(167, 139, 250, 0.1)" },
  semantic: { fill: "#22d3ee", glow: "rgba(34, 211, 238, 0.6)", bg: "rgba(34, 211, 238, 0.1)" },
  intent: { fill: "#fbbf24", glow: "rgba(251, 191, 36, 0.6)", bg: "rgba(251, 191, 36, 0.1)" },
}

interface Particle {
  edgeIndex: number
  progress: number
  speed: number
}

interface MemoryGraphProps {
  repoId?: string | null
}

export function MemoryGraph({ repoId }: MemoryGraphProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const [nodes, setNodes] = useState<GraphNode[]>([])
  const [edges, setEdges] = useState<GraphEdge[]>([])
  const [hoveredNode, setHoveredNode] = useState<string | null>(null)
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null)
  const [draggedNode, setDraggedNode] = useState<string | null>(null)
  const [dimensions, setDimensions] = useState({ width: 800, height: 600 })
  const animationRef = useRef<number | null>(null)
  const particlesRef = useRef<Particle[]>([])

  const [zoom, setZoom] = useState(1)
  const [pan, setPan] = useState({ x: 0, y: 0 })
  const [isPanning, setIsPanning] = useState(false)
  const lastPanPos = useRef({ x: 0, y: 0 })

  const [searchQuery, setSearchQuery] = useState("")
  const [activeFilters, setActiveFilters] = useState<MemoryLayer[]>(["episodic", "semantic", "intent"])
  const [showFilters, setShowFilters] = useState(false)
  const [loading, setLoading] = useState(true)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  // Fetch Graph Data
  useEffect(() => {
    async function fetchData() {
      setLoading(true);
      try {
        const data = await getGraphData(repoId);

        // Map API nodes to GraphNodes
        const newNodes: GraphNode[] = data.nodes.map((n: any) => ({
          id: n.id,
          label: n.label, // Short label
          layer: n.layer as MemoryLayer,
          description: n.full_label, // Use full content as description
          connections: 0, // Will calculate below
          createdAt: "2024-01-01", // Placeholder or fetch from metadata if available
          x: Math.random() * 800,
          y: Math.random() * 600,
          vx: 0,
          vy: 0
        }));

        // Map API links to GraphEdges
        const newEdges: GraphEdge[] = data.links.map((l: any) => ({
          source: l.source,
          target: l.target,
          strength: l.value
        }));

        // Calculate connection counts
        newNodes.forEach(node => {
          node.connections = newEdges.filter(e => e.source === node.id || e.target === node.id).length;
        });

        setNodes(newNodes);
        setEdges(newEdges);
        setErrorMessage(null);

        // Initialize particles
        particlesRef.current = newEdges.flatMap((_, idx) =>
          Array.from({ length: 3 }, () => ({
            edgeIndex: idx,
            progress: Math.random(),
            speed: 0.002 + Math.random() * 0.003,
          })),
        );

        setLoading(false);
      } catch (error) {
        console.error("Failed to fetch graph data:", error);
        setErrorMessage(describeApiError(error));
        setLoading(false);
      }
    }

    fetchData();
  }, [repoId]);

  const filteredNodes = nodes.filter((node) => {
    const matchesSearch =
      searchQuery === "" ||
      node.label.toLowerCase().includes(searchQuery.toLowerCase()) ||
      node.description?.toLowerCase().includes(searchQuery.toLowerCase())
    const matchesFilter = activeFilters.includes(node.layer)
    return matchesSearch && matchesFilter
  })

  const filteredNodeIds = new Set(filteredNodes.map((n) => n.id))

  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    const resizeObserver = new ResizeObserver((entries) => {
      for (const entry of entries) {
        setDimensions({
          width: entry.contentRect.width,
          height: entry.contentRect.height,
        })
      }
    })

    resizeObserver.observe(container)
    return () => resizeObserver.disconnect()
  }, [])

  const simulate = useCallback(() => {
    setNodes((prevNodes) => {
      const newNodes = prevNodes.map((node) => ({ ...node }))
      let totalEnergy = 0

      for (let i = 0; i < newNodes.length; i++) {
        const node = newNodes[i]
        if (node.id === draggedNode) continue

        // Repulsion between nodes (inverse square law)
        for (let j = 0; j < newNodes.length; j++) {
          if (i === j) continue
          const other = newNodes[j]
          const dx = node.x - other.x
          const dy = node.y - other.y
          let dist = Math.sqrt(dx * dx + dy * dy)
          if (dist < 1) dist = 1 // Prevent division by zero

          const force = 2000 / (dist * dist)
          const cappedForce = Math.min(force, 2) // Cap to prevent explosions

          node.vx += (dx / dist) * cappedForce
          node.vy += (dy / dist) * cappedForce
        }

        // Attraction along edges (spring force)
        for (const edge of edges) {
          let other: GraphNode | undefined
          if (edge.source === node.id) {
            other = newNodes.find((n) => n.id === edge.target)
          } else if (edge.target === node.id) {
            other = newNodes.find((n) => n.id === edge.source)
          }
          if (other) {
            const dx = other.x - node.x
            const dy = other.y - node.y
            const dist = Math.sqrt(dx * dx + dy * dy) || 1
            const force = (dist - 150) * 0.01
            node.vx += (dx / dist) * force
            node.vy += (dy / dist) * force
          }
        }

        // Center gravity - gentle pull to canvas center
        const centerX = dimensions.width / 2
        const centerY = dimensions.height / 2
        node.vx += (centerX - node.x) * 0.0003
        node.vy += (centerY - node.y) * 0.0003

        // Apply velocity with strong damping for quick settling
        node.vx *= 0.85
        node.vy *= 0.85
        node.x += node.vx
        node.y += node.vy

        // Track total kinetic energy
        totalEnergy += Math.abs(node.vx) + Math.abs(node.vy)
      }

      // Stop updating if energy is very low (system has settled)
      if (totalEnergy < 0.5 && !draggedNode) {
        return prevNodes
      }

      return newNodes
    })
  }, [draggedNode, dimensions, edges])

  const render = useCallback(() => {
    const canvas = canvasRef.current
    const ctx = canvas?.getContext("2d")
    if (!canvas || !ctx) return

    ctx.save()

    // Clear with dark background
    ctx.fillStyle = "#0a0a0f"
    ctx.fillRect(0, 0, canvas.width, canvas.height)

    ctx.translate(pan.x, pan.y)
    ctx.scale(zoom, zoom)

    // Draw subtle grid pattern
    ctx.strokeStyle = "rgba(255, 255, 255, 0.03)"
    ctx.lineWidth = 1 / zoom
    const gridSize = 40
    const startX = Math.floor(-pan.x / zoom / gridSize) * gridSize
    const startY = Math.floor(-pan.y / zoom / gridSize) * gridSize
    const endX = startX + canvas.width / zoom + gridSize * 2
    const endY = startY + canvas.height / zoom + gridSize * 2

    for (let x = startX; x < endX; x += gridSize) {
      ctx.beginPath()
      ctx.moveTo(x, startY)
      ctx.lineTo(x, endY)
      ctx.stroke()
    }
    for (let y = startY; y < endY; y += gridSize) {
      ctx.beginPath()
      ctx.moveTo(startX, y)
      ctx.lineTo(endX, y)
      ctx.stroke()
    }

    // Draw edges
    for (let edgeIdx = 0; edgeIdx < edges.length; edgeIdx++) {
      const edge = edges[edgeIdx]
      const sourceNode = nodes.find((n) => n.id === edge.source)
      const targetNode = nodes.find((n) => n.id === edge.target)
      if (!sourceNode || !targetNode) continue

      const sourceVisible = filteredNodeIds.has(sourceNode.id)
      const targetVisible = filteredNodeIds.has(targetNode.id)
      if (!sourceVisible && !targetVisible) continue

      const isHighlighted =
        hoveredNode === edge.source ||
        hoveredNode === edge.target ||
        selectedNode?.id === edge.source ||
        selectedNode?.id === edge.target
      const opacity = !sourceVisible || !targetVisible ? 0.05 : isHighlighted ? 0.6 : 0.15

      // Edge glow effect
      if (isHighlighted) {
        ctx.shadowColor = "rgba(255, 255, 255, 0.5)"
        ctx.shadowBlur = 8
      }

      const midX = (sourceNode.x + targetNode.x) / 2
      const midY = (sourceNode.y + targetNode.y) / 2
      const dx = targetNode.x - sourceNode.x
      const dy = targetNode.y - sourceNode.y
      const offset = Math.sqrt(dx * dx + dy * dy) * 0.1
      const ctrlX = midX - dy * 0.2
      const ctrlY = midY + dx * 0.2

      ctx.beginPath()
      ctx.moveTo(sourceNode.x, sourceNode.y)
      ctx.quadraticCurveTo(ctrlX, ctrlY, targetNode.x, targetNode.y)
      ctx.strokeStyle = `rgba(255, 255, 255, ${opacity})`
      ctx.lineWidth = ((edge.strength || 0.5) * 2 + 0.5) / zoom
      ctx.stroke()

      ctx.shadowColor = "transparent"
      ctx.shadowBlur = 0

      if (isHighlighted && sourceVisible && targetVisible) {
        const edgeParticles = particlesRef.current.filter((p) => p.edgeIndex === edgeIdx)
        for (const particle of edgeParticles) {
          particle.progress += particle.speed
          if (particle.progress > 1) particle.progress = 0

          const t = particle.progress
          const px = (1 - t) * (1 - t) * sourceNode.x + 2 * (1 - t) * t * ctrlX + t * t * targetNode.x
          const py = (1 - t) * (1 - t) * sourceNode.y + 2 * (1 - t) * t * ctrlY + t * t * targetNode.y

          ctx.beginPath()
          ctx.arc(px, py, 2 / zoom, 0, Math.PI * 2)
          ctx.fillStyle = "rgba(255, 255, 255, 0.8)"
          ctx.fill()
        }
      }
    }

    // Draw nodes
    for (const node of nodes) {
      const isFiltered = filteredNodeIds.has(node.id)
      const colors = layerColors[node.layer]
      const isHovered = hoveredNode === node.id
      const isSelected = selectedNode?.id === node.id
      const isConnected =
        (hoveredNode || selectedNode?.id) &&
        edges.some(
          (e) =>
            (e.source === (hoveredNode || selectedNode?.id) && e.target === node.id) ||
            (e.target === (hoveredNode || selectedNode?.id) && e.source === node.id),
        )
      const shouldHighlight = isHovered || isSelected || isConnected

      const baseRadius = isHovered || isSelected ? 12 : 8
      const pulseRadius = baseRadius + Math.sin(Date.now() / 500) * (isHovered ? 2 : 1)

      // Dim non-filtered nodes
      const nodeOpacity = isFiltered ? 1 : 0.2

      // Outer glow
      if (shouldHighlight && isFiltered) {
        const gradient = ctx.createRadialGradient(node.x, node.y, 0, node.x, node.y, pulseRadius * 4)
        gradient.addColorStop(0, colors.glow)
        gradient.addColorStop(0.5, colors.glow.replace("0.6", "0.2"))
        gradient.addColorStop(1, "transparent")

        ctx.beginPath()
        ctx.arc(node.x, node.y, pulseRadius * 4, 0, Math.PI * 2)
        ctx.fillStyle = gradient
        ctx.fill()
      }

      // Node circle with glow
      ctx.shadowColor = isFiltered ? colors.glow : "transparent"
      ctx.shadowBlur = shouldHighlight ? 20 : 10
      ctx.globalAlpha = nodeOpacity

      ctx.beginPath()
      ctx.arc(node.x, node.y, pulseRadius, 0, Math.PI * 2)
      ctx.fillStyle = colors.fill
      ctx.fill()

      if (isSelected) {
        ctx.beginPath()
        ctx.arc(node.x, node.y, pulseRadius + 4, 0, Math.PI * 2)
        ctx.strokeStyle = "rgba(255, 255, 255, 0.8)"
        ctx.lineWidth = 2 / zoom
        ctx.stroke()
      }

      ctx.shadowColor = "transparent"
      ctx.shadowBlur = 0
      ctx.globalAlpha = 1

      // Label - always show for filtered nodes
      if (isFiltered && (shouldHighlight || zoom > 0.8)) {
        ctx.font = `${12 / zoom}px Inter, system-ui, sans-serif`
        ctx.textAlign = "center"
        ctx.fillStyle = `rgba(255, 255, 255, ${shouldHighlight ? 0.9 : 0.6})`
        ctx.fillText(node.label, node.x, node.y - pulseRadius - 10 / zoom)
      }
    }

    ctx.restore()

    const minimapSize = 120
    const minimapPadding = 12
    const minimapX = canvas.width - minimapSize - minimapPadding
    const minimapY = canvas.height - minimapSize - minimapPadding
    const minimapScale = minimapSize / Math.max(dimensions.width, dimensions.height)

    ctx.fillStyle = "rgba(0, 0, 0, 0.6)"
    ctx.strokeStyle = "rgba(255, 255, 255, 0.2)"
    ctx.lineWidth = 1
    ctx.beginPath()
    ctx.roundRect(minimapX - 4, minimapY - 4, minimapSize + 8, minimapSize + 8, 8)
    ctx.fill()
    ctx.stroke()

    // Minimap nodes
    for (const node of nodes) {
      if (!filteredNodeIds.has(node.id)) continue
      const colors = layerColors[node.layer]
      const mx = minimapX + node.x * minimapScale
      const my = minimapY + node.y * minimapScale

      ctx.beginPath()
      ctx.arc(mx, my, 2, 0, Math.PI * 2)
      ctx.fillStyle = colors.fill
      ctx.fill()
    }

    // Minimap viewport indicator
    ctx.strokeStyle = "rgba(255, 255, 255, 0.5)"
    ctx.lineWidth = 1
    ctx.strokeRect(
      minimapX + (-pan.x / zoom) * minimapScale,
      minimapY + (-pan.y / zoom) * minimapScale,
      (canvas.width / zoom) * minimapScale,
      (canvas.height / zoom) * minimapScale,
    )

    simulate()
    animationRef.current = requestAnimationFrame(render)
  }, [nodes, hoveredNode, selectedNode, simulate, zoom, pan, filteredNodeIds, dimensions])

  useEffect(() => {
    animationRef.current = requestAnimationFrame(render)
    return () => {
      if (animationRef.current) {
        cancelAnimationFrame(animationRef.current)
      }
    }
  }, [render])

  const screenToCanvas = useCallback(
    (screenX: number, screenY: number) => {
      return {
        x: (screenX - pan.x) / zoom,
        y: (screenY - pan.y) / zoom,
      }
    },
    [pan, zoom],
  )

  const getNodeAtPosition = useCallback(
    (x: number, y: number) => {
      const canvasPos = screenToCanvas(x, y)
      for (const node of nodes) {
        const dx = canvasPos.x - node.x
        const dy = canvasPos.y - node.y
        if (Math.sqrt(dx * dx + dy * dy) < 15) {
          return node
        }
      }
      return null
    },
    [nodes, screenToCanvas],
  )

  const handleMouseMove = useCallback(
    (e: React.MouseEvent<HTMLCanvasElement>) => {
      const canvas = canvasRef.current
      if (!canvas) return

      const rect = canvas.getBoundingClientRect()
      const x = e.clientX - rect.left
      const y = e.clientY - rect.top

      if (isPanning) {
        const dx = x - lastPanPos.current.x
        const dy = y - lastPanPos.current.y
        setPan((prev) => ({ x: prev.x + dx, y: prev.y + dy }))
        lastPanPos.current = { x, y }
      } else if (draggedNode) {
        const canvasPos = screenToCanvas(x, y)
        setNodes((prev) =>
          prev.map((node) =>
            node.id === draggedNode ? { ...node, x: canvasPos.x, y: canvasPos.y, vx: 0, vy: 0 } : node,
          ),
        )
      } else {
        const node = getNodeAtPosition(x, y)
        setHoveredNode(node?.id || null)
      }
    },
    [draggedNode, getNodeAtPosition, isPanning, screenToCanvas],
  )

  const handleMouseDown = useCallback(
    (e: React.MouseEvent<HTMLCanvasElement>) => {
      const canvas = canvasRef.current
      if (!canvas) return

      const rect = canvas.getBoundingClientRect()
      const x = e.clientX - rect.left
      const y = e.clientY - rect.top
      const node = getNodeAtPosition(x, y)

      if (node) {
        if (e.detail === 2) {
          // Double click to select
          setSelectedNode(node)
        } else {
          setDraggedNode(node.id)
        }
      } else {
        // Start panning
        setIsPanning(true)
        lastPanPos.current = { x, y }
        setSelectedNode(null)
      }
    },
    [getNodeAtPosition],
  )

  const handleMouseUp = useCallback(() => {
    setDraggedNode(null)
    setIsPanning(false)
  }, [])

  const handleWheel = useCallback(
    (e: React.WheelEvent<HTMLCanvasElement>) => {
      e.preventDefault()
      const delta = e.deltaY > 0 ? 0.9 : 1.1
      const newZoom = Math.min(Math.max(zoom * delta, 0.3), 3)

      // Zoom toward mouse position
      const rect = canvasRef.current?.getBoundingClientRect()
      if (rect) {
        const mouseX = e.clientX - rect.left
        const mouseY = e.clientY - rect.top
        const zoomRatio = newZoom / zoom

        setPan((prev) => ({
          x: mouseX - (mouseX - prev.x) * zoomRatio,
          y: mouseY - (mouseY - prev.y) * zoomRatio,
        }))
      }

      setZoom(newZoom)
    },
    [zoom],
  )

  const handleZoomIn = () => setZoom((prev) => Math.min(prev * 1.2, 3))
  const handleZoomOut = () => setZoom((prev) => Math.max(prev / 1.2, 0.3))
  const handleResetView = () => {
    setZoom(1)
    setPan({ x: 0, y: 0 })
  }

  const toggleFilter = (layer: MemoryLayer) => {
    setActiveFilters((prev) => (prev.includes(layer) ? prev.filter((l) => l !== layer) : [...prev, layer]))
  }

  const getConnectedNodes = (nodeId: string) => {
    const connected = new Set<string>()
    edges.forEach((edge: GraphEdge) => {
      if (edge.source === nodeId) connected.add(edge.target)
      if (edge.target === nodeId) connected.add(edge.source)
    })
    return nodes.filter((n) => connected.has(n.id))
  }

  if (errorMessage) {
    return (
      <div className="glass flex min-h-[360px] items-center justify-center rounded-xl p-6">
        <div className="max-w-md text-center text-sm text-muted-foreground">
          <AlertTriangle className="mx-auto mb-3 h-6 w-6 text-destructive" />
          <p className="font-medium text-foreground">Graph data is unavailable</p>
          <p className="mt-2">{errorMessage}</p>
        </div>
      </div>
    )
  }

  return (
    <motion.div
      ref={containerRef}
      initial={{ opacity: 0, scale: 0.95 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ duration: 0.5 }}
      className="relative w-full h-[700px] rounded-2xl overflow-hidden border border-white/10 bg-[#0a0a0f]"
    >
      <canvas
        ref={canvasRef}
        width={dimensions.width}
        height={dimensions.height}
        onMouseMove={handleMouseMove}
        onMouseDown={handleMouseDown}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseUp}
        onWheel={handleWheel}
        className={isPanning ? "cursor-grabbing" : draggedNode ? "cursor-grabbing" : "cursor-grab"}
        style={{ width: "100%", height: "100%" }}
      />

      <div className="absolute top-4 left-4 flex items-center gap-2">
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-white/40" />
          <input
            type="text"
            placeholder="Search nodes..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-64 pl-10 pr-4 py-2 rounded-lg bg-black/50 backdrop-blur-sm border border-white/10 text-sm text-white placeholder:text-white/40 focus:outline-none focus:border-white/30"
          />
          {searchQuery && (
            <button
              onClick={() => setSearchQuery("")}
              className="absolute right-3 top-1/2 -translate-y-1/2 text-white/40 hover:text-white/70"
            >
              <X className="w-4 h-4" />
            </button>
          )}
        </div>

        <button
          onClick={() => setShowFilters(!showFilters)}
          className={`p-2 rounded-lg backdrop-blur-sm border transition-colors ${showFilters ? "bg-white/10 border-white/20" : "bg-black/50 border-white/10 hover:border-white/20"
            }`}
        >
          <Filter className="w-4 h-4 text-white/70" />
        </button>

        <AnimatePresence>
          {showFilters && (
            <motion.div
              initial={{ opacity: 0, x: -10 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: -10 }}
              className="flex gap-2"
            >
              {(Object.keys(layerColors) as MemoryLayer[]).map((layer) => (
                <button
                  key={layer}
                  onClick={() => toggleFilter(layer)}
                  className={`px-3 py-1.5 rounded-lg text-xs font-medium capitalize backdrop-blur-sm border transition-all ${activeFilters.includes(layer) ? "border-white/30" : "border-white/10 opacity-50"
                    }`}
                  style={{
                    backgroundColor: activeFilters.includes(layer) ? layerColors[layer].bg : "rgba(0,0,0,0.5)",
                    color: layerColors[layer].fill,
                  }}
                >
                  {layer}
                </button>
              ))}
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      <div className="absolute top-4 right-4 flex flex-col gap-2">
        <button
          onClick={handleZoomIn}
          className="p-2 rounded-lg bg-black/50 backdrop-blur-sm border border-white/10 hover:border-white/20 transition-colors"
        >
          <ZoomIn className="w-4 h-4 text-white/70" />
        </button>
        <button
          onClick={handleZoomOut}
          className="p-2 rounded-lg bg-black/50 backdrop-blur-sm border border-white/10 hover:border-white/20 transition-colors"
        >
          <ZoomOut className="w-4 h-4 text-white/70" />
        </button>
        <button
          onClick={handleResetView}
          className="p-2 rounded-lg bg-black/50 backdrop-blur-sm border border-white/10 hover:border-white/20 transition-colors"
        >
          <Maximize2 className="w-4 h-4 text-white/70" />
        </button>
        <div className="text-xs text-white/40 text-center mt-1">{Math.round(zoom * 100)}%</div>
      </div>

      <AnimatePresence>
        {selectedNode && (
          <motion.div
            initial={{ opacity: 0, x: 20 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: 20 }}
            className="absolute top-16 right-4 w-72 rounded-xl bg-black/70 backdrop-blur-md border border-white/10 overflow-hidden"
          >
            <div className="h-1" style={{ backgroundColor: layerColors[selectedNode.layer].fill }} />
            <div className="p-4">
              <div className="flex items-start justify-between mb-3">
                <div>
                  <h3 className="font-semibold text-white">{selectedNode.label}</h3>
                  <span
                    className="text-xs px-2 py-0.5 rounded-full capitalize"
                    style={{
                      backgroundColor: layerColors[selectedNode.layer].bg,
                      color: layerColors[selectedNode.layer].fill,
                    }}
                  >
                    {selectedNode.layer}
                  </span>
                </div>
                <button
                  onClick={() => setSelectedNode(null)}
                  className="p-1 rounded hover:bg-white/10 transition-colors"
                >
                  <X className="w-4 h-4 text-white/50" />
                </button>
              </div>

              {selectedNode.description && <p className="text-sm text-white/70 mb-4">{selectedNode.description}</p>}

              <div className="space-y-2 text-xs text-white/50">
                <div className="flex items-center gap-2">
                  <Link2 className="w-3 h-3" />
                  <span>{selectedNode.connections} connections</span>
                </div>
                {selectedNode.createdAt && (
                  <div className="flex items-center gap-2">
                    <Clock className="w-3 h-3" />
                    <span>Created {selectedNode.createdAt}</span>
                  </div>
                )}
              </div>

              {/* Connected nodes */}
              <div className="mt-4 pt-3 border-t border-white/10">
                <div className="flex items-center gap-2 mb-2">
                  <Tag className="w-3 h-3 text-white/50" />
                  <span className="text-xs text-white/50">Connected to</span>
                </div>
                <div className="flex flex-wrap gap-1">
                  {getConnectedNodes(selectedNode.id).map((node) => (
                    <button
                      key={node.id}
                      onClick={() => setSelectedNode(node)}
                      className="text-xs px-2 py-1 rounded bg-white/5 hover:bg-white/10 transition-colors"
                      style={{ color: layerColors[node.layer].fill }}
                    >
                      {node.label}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Legend */}
      <div className="absolute bottom-4 left-4 flex gap-4 px-4 py-2 rounded-lg bg-black/50 backdrop-blur-sm border border-white/10">
        {Object.entries(layerColors).map(([layer, colors]) => (
          <div key={layer} className="flex items-center gap-2">
            <div
              className="w-3 h-3 rounded-full"
              style={{ backgroundColor: colors.fill, boxShadow: `0 0 8px ${colors.glow}` }}
            />
            <span className="text-xs text-white/70 capitalize">{layer}</span>
          </div>
        ))}
      </div>

      {/* Instructions */}
      <div className="absolute bottom-4 right-4 text-xs text-white/40 text-right">
        Scroll to zoom | Drag canvas to pan | Double-click node for details
      </div>
    </motion.div>
  )
}
