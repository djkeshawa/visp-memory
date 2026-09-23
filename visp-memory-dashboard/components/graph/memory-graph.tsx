"use client"

import type React from "react"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { motion, AnimatePresence } from "framer-motion"
import { DEFAULT_GRAPH_LAYERS, MEMORY_LAYER_CONFIG, MEMORY_LAYERS } from "@/lib/layers"
import type { MemoryLayer, RelationshipEvidence } from "@/lib/types"
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
  label?: string
  evidence?: RelationshipEvidence | null
}

const layerColors = MEMORY_LAYER_CONFIG
const getLayerColors = (layer: string) => layerColors[layer as MemoryLayer] ?? layerColors.episodic

interface Particle {
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
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const [draggedNode, setDraggedNode] = useState<string | null>(null)
  const [dimensions, setDimensions] = useState({ width: 800, height: 600 })
  const animationRef = useRef<number | null>(null)
  const particlesRef = useRef<Map<number, Particle[]>>(new Map())
  const settledRef = useRef(false)
  const [isSettled, setIsSettled] = useState(false)
  const requestGenerationRef = useRef(0)
  const repoIdRef = useRef(repoId)
  const syncedRepoIdRef = useRef(repoId)
  const nodesRef = useRef(nodes)
  const edgesRef = useRef(edges)
  const hoveredNodeRef = useRef(hoveredNode)
  const selectedNodeIdRef = useRef(selectedNodeId)
  const draggedNodeRef = useRef(draggedNode)
  const dimensionsRef = useRef(dimensions)
  const zoomRef = useRef(1)
  const panRef = useRef({ x: 0, y: 0 })
  const filteredNodeIdsRef = useRef<Set<string>>(new Set())
  const nodeMapRef = useRef<Map<string, GraphNode>>(new Map())
  const adjacencyMapRef = useRef<Map<string, Set<string>>>(new Map())

  const [zoom, setZoom] = useState(1)
  const [pan, setPan] = useState({ x: 0, y: 0 })
  const [isPanning, setIsPanning] = useState(false)
  const lastPanPos = useRef({ x: 0, y: 0 })

  const [searchQuery, setSearchQuery] = useState("")
  const [activeFilters, setActiveFilters] = useState<MemoryLayer[]>([...DEFAULT_GRAPH_LAYERS])
  const [showFilters, setShowFilters] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  const nodeMap = useMemo(() => new Map(nodes.map((node) => [node.id, node])), [nodes])
  const adjacencyMap = useMemo(() => {
    const map = new Map<string, Set<string>>()
    for (const node of nodes) map.set(node.id, new Set())
    for (const edge of edges) {
      map.get(edge.source)?.add(edge.target)
      map.get(edge.target)?.add(edge.source)
    }
    return map
  }, [edges, nodes])

  repoIdRef.current = repoId

  // Fetch graph data. A generation token prevents a slow response for the previous repository
  // from replacing the graph after the user has already selected another repository.
  useEffect(() => {
    const generation = ++requestGenerationRef.current
    const requestedRepoId = repoId
    settledRef.current = false
    setIsSettled(false)
    setNodes([])
    setEdges([])
    nodesRef.current = []
    edgesRef.current = []
    nodeMapRef.current = new Map()
    adjacencyMapRef.current = new Map()
    particlesRef.current = new Map()
    setSelectedNodeId(null)
    selectedNodeIdRef.current = null
    setHoveredNode(null)
    hoveredNodeRef.current = null
    setDraggedNode(null)
    draggedNodeRef.current = null
    setErrorMessage(null)

    async function fetchData() {
      try {
        const data = await getGraphData(requestedRepoId)
        if (generation !== requestGenerationRef.current || repoIdRef.current !== requestedRepoId) return

        const connectionCounts = new Map<string, number>()
        const newNodes: GraphNode[] = data.nodes.map((node) => ({
          id: node.id,
          label: node.label,
          layer: node.layer,
          description: node.full_label,
          connections: 0,
          // createdAt intentionally omitted: the graph API does not return it.
          x: Math.random() * Math.max(dimensionsRef.current.width, 1),
          y: Math.random() * Math.max(dimensionsRef.current.height, 1),
          vx: 0,
          vy: 0,
        }))

        const newEdges: GraphEdge[] = data.links.map((link) => ({
          source: link.source,
          target: link.target,
          strength: link.value,
          label: link.label,
          evidence: link.evidence,
        }))

        // Calculate connection counts once while normalizing the response, not during every draw.
        for (const edge of newEdges) {
          connectionCounts.set(edge.source, (connectionCounts.get(edge.source) || 0) + 1)
          connectionCounts.set(edge.target, (connectionCounts.get(edge.target) || 0) + 1)
        }
        for (const node of newNodes) node.connections = connectionCounts.get(node.id) || 0

        setNodes(newNodes)
        setEdges(newEdges)
        setErrorMessage(null)

        // Keep particles indexed by edge so highlighted drawing does not filter the full list for
        // every edge on every frame.
        particlesRef.current = new Map(
          newEdges.map((_, index) => [
            index,
            Array.from({ length: 3 }, () => ({
              progress: Math.random(),
              speed: 0.002 + Math.random() * 0.003,
            })),
          ]),
        )
      } catch (error) {
        if (generation !== requestGenerationRef.current || repoIdRef.current !== requestedRepoId) return
        console.error("Failed to fetch graph data:", error)
        setErrorMessage(describeApiError(error))
      }
    }

    void fetchData()
  }, [repoId])

  const filteredNodes = nodes.filter((node) => {
    const matchesSearch =
      searchQuery === "" ||
      node.label.toLowerCase().includes(searchQuery.toLowerCase()) ||
      node.description?.toLowerCase().includes(searchQuery.toLowerCase())
    const matchesFilter = activeFilters.includes(node.layer)
    return matchesSearch && matchesFilter
  })

  const filteredNodeIds = useMemo(() => new Set(filteredNodes.map((n) => n.id)), [filteredNodes])

  useEffect(() => {
    if (syncedRepoIdRef.current !== repoId) {
      syncedRepoIdRef.current = repoId
      nodesRef.current = []
      edgesRef.current = []
      hoveredNodeRef.current = null
      selectedNodeIdRef.current = null
      draggedNodeRef.current = null
      nodeMapRef.current = new Map()
      adjacencyMapRef.current = new Map()
      filteredNodeIdsRef.current = new Set()
      return
    }
    nodesRef.current = nodes
    edgesRef.current = edges
    hoveredNodeRef.current = hoveredNode
    selectedNodeIdRef.current = selectedNodeId
    draggedNodeRef.current = draggedNode
    dimensionsRef.current = dimensions
    zoomRef.current = zoom
    panRef.current = pan
    filteredNodeIdsRef.current = filteredNodeIds
    nodeMapRef.current = nodeMap
    adjacencyMapRef.current = adjacencyMap
  }, [adjacencyMap, dimensions, draggedNode, edges, filteredNodeIds, hoveredNode, nodeMap, nodes, pan, repoId, selectedNodeId, zoom])

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

  const renderRef = useRef<() => void>(() => undefined)
  const scheduleRender = useCallback(() => {
    if (animationRef.current !== null || !canvasRef.current) return
    animationRef.current = requestAnimationFrame(() => {
      animationRef.current = null
      renderRef.current()
    })
  }, [])

  const simulate = useCallback(() => {
    if (settledRef.current && !draggedNodeRef.current) return

    const generation = requestGenerationRef.current
    const currentNodes = nodesRef.current
    if (currentNodes.length === 0) {
      settledRef.current = true
      setIsSettled(true)
      return
    }

    const nextNodes = currentNodes.map((node) => ({ ...node }))
    const nextNodeMap = new Map(nextNodes.map((node) => [node.id, node]))
    const activeDraggedNode = draggedNodeRef.current
    const { width, height } = dimensionsRef.current
    let totalEnergy = 0

    for (const node of nextNodes) {
      if (node.id === activeDraggedNode) continue

      // Repulsion between nodes (inverse square law).
      for (const other of nextNodes) {
        if (node.id === other.id) continue
        const dx = node.x - other.x
        const dy = node.y - other.y
        const distance = Math.max(Math.sqrt(dx * dx + dy * dy), 1)
        const force = Math.min(2000 / (distance * distance), 2)
        node.vx += (dx / distance) * force
        node.vy += (dy / distance) * force
      }

      // Attraction along the precomputed adjacency map.
      for (const neighborId of adjacencyMapRef.current.get(node.id) || []) {
        const other = nextNodeMap.get(neighborId)
        if (!other) continue
        const dx = other.x - node.x
        const dy = other.y - node.y
        const distance = Math.sqrt(dx * dx + dy * dy) || 1
        const force = (distance - 150) * 0.01
        node.vx += (dx / distance) * force
        node.vy += (dy / distance) * force
      }

      node.vx += (width / 2 - node.x) * 0.0003
      node.vy += (height / 2 - node.y) * 0.0003
      node.vx *= 0.85
      node.vy *= 0.85
      node.x += node.vx
      node.y += node.vy
      totalEnergy += Math.abs(node.vx) + Math.abs(node.vy)
    }

    const nowSettled = totalEnergy < 0.5 && !activeDraggedNode
    if (nowSettled) {
      for (const node of nextNodes) {
        node.vx = 0
        node.vy = 0
      }
      settledRef.current = true
      setIsSettled(true)
    }
    if (generation !== requestGenerationRef.current) return
    nodesRef.current = nextNodes
    setNodes((current) => (generation === requestGenerationRef.current ? nextNodes : current))
  }, [])

  const render = useCallback(() => {
    const canvas = canvasRef.current
    const ctx = canvas?.getContext("2d")
    if (!canvas || !ctx) return

    const currentNodes = nodesRef.current
    const currentEdges = edgesRef.current
    const currentHoveredNode = hoveredNodeRef.current
    const currentSelectedNodeId = selectedNodeIdRef.current
    const currentFilteredNodeIds = filteredNodeIdsRef.current
    const { width, height } = dimensionsRef.current
    const currentZoom = zoomRef.current
    const currentPan = panRef.current

    ctx.save()
    ctx.fillStyle = "#0a0a0f"
    ctx.fillRect(0, 0, canvas.width, canvas.height)
    ctx.translate(currentPan.x, currentPan.y)
    ctx.scale(currentZoom, currentZoom)

    ctx.strokeStyle = "rgba(255, 255, 255, 0.03)"
    ctx.lineWidth = 1 / currentZoom
    const gridSize = 40
    const startX = Math.floor(-currentPan.x / currentZoom / gridSize) * gridSize
    const startY = Math.floor(-currentPan.y / currentZoom / gridSize) * gridSize
    const endX = startX + canvas.width / currentZoom + gridSize * 2
    const endY = startY + canvas.height / currentZoom + gridSize * 2

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

    for (let edgeIndex = 0; edgeIndex < currentEdges.length; edgeIndex += 1) {
      const edge = currentEdges[edgeIndex]
      const sourceNode = nodeMapRef.current.get(edge.source)
      const targetNode = nodeMapRef.current.get(edge.target)
      if (!sourceNode || !targetNode) continue

      const sourceVisible = currentFilteredNodeIds.has(sourceNode.id)
      const targetVisible = currentFilteredNodeIds.has(targetNode.id)
      if (!sourceVisible && !targetVisible) continue

      const isHighlighted =
        currentHoveredNode === edge.source ||
        currentHoveredNode === edge.target ||
        currentSelectedNodeId === edge.source ||
        currentSelectedNodeId === edge.target
      const opacity = !sourceVisible || !targetVisible ? 0.05 : isHighlighted ? 0.6 : 0.15

      if (isHighlighted) {
        ctx.shadowColor = "rgba(255, 255, 255, 0.5)"
        ctx.shadowBlur = 8
      }

      const midX = (sourceNode.x + targetNode.x) / 2
      const midY = (sourceNode.y + targetNode.y) / 2
      const dx = targetNode.x - sourceNode.x
      const dy = targetNode.y - sourceNode.y
      const ctrlX = midX - dy * 0.2
      const ctrlY = midY + dx * 0.2

      ctx.beginPath()
      ctx.moveTo(sourceNode.x, sourceNode.y)
      ctx.quadraticCurveTo(ctrlX, ctrlY, targetNode.x, targetNode.y)
      ctx.strokeStyle = `rgba(255, 255, 255, ${opacity})`
      ctx.lineWidth = ((edge.strength || 0.5) * 2 + 0.5) / currentZoom
      ctx.stroke()
      ctx.shadowColor = "transparent"
      ctx.shadowBlur = 0

      if (isHighlighted && sourceVisible && targetVisible) {
        for (const particle of particlesRef.current.get(edgeIndex) || []) {
          particle.progress += particle.speed
          if (particle.progress > 1) particle.progress = 0
          const t = particle.progress
          const px = (1 - t) * (1 - t) * sourceNode.x + 2 * (1 - t) * t * ctrlX + t * t * targetNode.x
          const py = (1 - t) * (1 - t) * sourceNode.y + 2 * (1 - t) * t * ctrlY + t * t * targetNode.y
          ctx.beginPath()
          ctx.arc(px, py, 2 / currentZoom, 0, Math.PI * 2)
          ctx.fillStyle = "rgba(255, 255, 255, 0.8)"
          ctx.fill()
        }
      }
    }

    for (const node of currentNodes) {
      const isFiltered = currentFilteredNodeIds.has(node.id)
      const colors = getLayerColors(node.layer)
      const isHovered = currentHoveredNode === node.id
      const isSelected = currentSelectedNodeId === node.id
      const isConnected = Boolean(
        (currentHoveredNode || currentSelectedNodeId) &&
          adjacencyMapRef.current.get(currentHoveredNode || currentSelectedNodeId || "")?.has(node.id),
      )
      const shouldHighlight = isHovered || isSelected || isConnected
      const baseRadius = isHovered || isSelected ? 12 : 8
      const pulseRadius = baseRadius + Math.sin(Date.now() / 500) * (isHovered ? 2 : 1)
      const nodeOpacity = isFiltered ? 1 : 0.2

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
        ctx.lineWidth = 2 / currentZoom
        ctx.stroke()
      }

      ctx.shadowColor = "transparent"
      ctx.shadowBlur = 0
      ctx.globalAlpha = 1
      if (isFiltered && (shouldHighlight || currentZoom > 0.8)) {
        ctx.font = `${12 / currentZoom}px Inter, system-ui, sans-serif`
        ctx.textAlign = "center"
        ctx.fillStyle = `rgba(255, 255, 255, ${shouldHighlight ? 0.9 : 0.6})`
        ctx.fillText(node.label, node.x, node.y - pulseRadius - 10 / currentZoom)
      }
    }
    ctx.restore()

    const minimapSize = 120
    const minimapPadding = 12
    const minimapX = canvas.width - minimapSize - minimapPadding
    const minimapY = canvas.height - minimapSize - minimapPadding
    const minimapScale = minimapSize / Math.max(width, height, 1)
    ctx.fillStyle = "rgba(0, 0, 0, 0.6)"
    ctx.strokeStyle = "rgba(255, 255, 255, 0.2)"
    ctx.lineWidth = 1
    ctx.beginPath()
    ctx.roundRect(minimapX - 4, minimapY - 4, minimapSize + 8, minimapSize + 8, 8)
    ctx.fill()
    ctx.stroke()

    for (const node of currentNodes) {
      if (!currentFilteredNodeIds.has(node.id)) continue
      const colors = getLayerColors(node.layer)
      ctx.beginPath()
      ctx.arc(minimapX + node.x * minimapScale, minimapY + node.y * minimapScale, 2, 0, Math.PI * 2)
      ctx.fillStyle = colors.fill
      ctx.fill()
    }

    ctx.strokeStyle = "rgba(255, 255, 255, 0.5)"
    ctx.lineWidth = 1
    ctx.strokeRect(
      minimapX + (-currentPan.x / currentZoom) * minimapScale,
      minimapY + (-currentPan.y / currentZoom) * minimapScale,
      (canvas.width / currentZoom) * minimapScale,
      (canvas.height / currentZoom) * minimapScale,
    )

    simulate()
    const hasAnimatedParticles = Boolean(currentHoveredNode || currentSelectedNodeId)
    if (!settledRef.current || hasAnimatedParticles || draggedNodeRef.current) scheduleRender()
  }, [scheduleRender, simulate])

  renderRef.current = render

  // Data, filters, resizing, and dragging are the only events that restart the force layout.
  useEffect(() => {
    if (!nodes.length) return
    settledRef.current = false
    setIsSettled(false)
    scheduleRender()
  }, [activeFilters, dimensions.height, dimensions.width, nodes.length, scheduleRender, searchQuery])

  useEffect(() => {
    scheduleRender()
    return () => {
      if (animationRef.current !== null) {
        cancelAnimationFrame(animationRef.current)
        animationRef.current = null
      }
    }
  }, [scheduleRender])

  useEffect(() => {
    scheduleRender()
  }, [edges, filteredNodeIds, hoveredNode, pan, selectedNodeId, scheduleRender, zoom])

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
          setSelectedNodeId(node.id)
          selectedNodeIdRef.current = node.id
        } else {
          draggedNodeRef.current = node.id
          setDraggedNode(node.id)
          settledRef.current = false
          setIsSettled(false)
          scheduleRender()
        }
      } else {
        // Start panning
        setIsPanning(true)
        lastPanPos.current = { x, y }
        setSelectedNodeId(null)
        selectedNodeIdRef.current = null
      }
    },
    [getNodeAtPosition, scheduleRender],
  )

  const handleMouseUp = useCallback(() => {
    if (draggedNodeRef.current) {
      settledRef.current = false
      setIsSettled(false)
    }
    draggedNodeRef.current = null
    setDraggedNode(null)
    setIsPanning(false)
    scheduleRender()
  }, [scheduleRender])

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
    return Array.from(adjacencyMap.get(nodeId) || [])
      .map((connectedId) => nodeMap.get(connectedId))
      .filter((node): node is GraphNode => Boolean(node))
  }

  const getConnectedEdges = (nodeId: string) => {
    return edges.filter((edge) => edge.source === nodeId || edge.target === nodeId)
  }

  const getNodeLabel = (nodeId: string) => {
    return nodeMap.get(nodeId)?.label || nodeId
  }

  const describeEvidence = (evidence?: RelationshipEvidence | null) => {
    if (!evidence) return "No relationship evidence recorded."

    const parts = []
    if (evidence.confidence) parts.push(evidence.confidence)
    if (typeof evidence.confidence_score === "number") parts.push(`${Math.round(evidence.confidence_score * 100)}%`)
    if (evidence.source) parts.push(evidence.source)
    return parts.length ? parts.join(" · ") : "No relationship evidence recorded."
  }

  const selectedNode = selectedNodeId ? nodeMap.get(selectedNodeId) || null : null
  const selectedEdges = selectedNode ? getConnectedEdges(selectedNode.id) : []

  useEffect(() => {
    if (selectedNodeId && !nodeMap.has(selectedNodeId)) {
      setSelectedNodeId(null)
      selectedNodeIdRef.current = null
    }
  }, [nodeMap, selectedNodeId])

  if (errorMessage) {
    return (
      <div className="glass flex min-h-[360px] items-center justify-center rounded-lg p-6">
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
      className="relative h-[700px] w-full overflow-hidden rounded-lg border border-white/10 bg-[#0a0a0f]"
    >
      <canvas
        ref={canvasRef}
        width={Math.max(1, Math.floor(dimensions.width))}
        height={Math.max(1, Math.floor(dimensions.height))}
        aria-label="Interactive memory graph"
        onMouseMove={handleMouseMove}
        onMouseDown={handleMouseDown}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseUp}
        onWheel={handleWheel}
        className={isPanning ? "cursor-grabbing" : draggedNode ? "cursor-grabbing" : "cursor-grab"}
        style={{ width: "100%", height: "100%" }}
      />

      <span className="sr-only" data-testid="graph-node-labels">
        {filteredNodes.map((node) => node.label).join(" | ")}
      </span>
      <span className="sr-only" data-testid="graph-link-count">
        {edges.length} graph link{edges.length === 1 ? "" : "s"}
      </span>
      <div className="sr-only" data-testid="graph-node-controls" aria-label="Graph node controls">
        {filteredNodes.map((node) => (
          <button
            type="button"
            key={node.id}
            data-testid={`graph-node-control-${node.id}`}
            aria-label={`Select graph node ${node.label}`}
            onClick={() => {
              setSelectedNodeId(node.id)
              selectedNodeIdRef.current = node.id
              scheduleRender()
            }}
          >
            {node.label}
          </button>
        ))}
      </div>

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
          type="button"
          aria-label="Show graph filters"
          aria-expanded={showFilters}
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
              {MEMORY_LAYERS.map((layer) => (
                <button
                  type="button"
                  key={layer}
                  onClick={() => toggleFilter(layer)}
                  aria-pressed={activeFilters.includes(layer)}
                  data-testid={`graph-filter-${layer}`}
                  className={`px-3 py-1.5 rounded-lg text-xs font-medium capitalize backdrop-blur-sm border transition-all ${activeFilters.includes(layer) ? "border-white/30" : "border-white/10 opacity-50"
                    }`}
                  style={{
                    backgroundColor: activeFilters.includes(layer) ? layerColors[layer].bg : "rgba(0,0,0,0.5)",
                    color: layerColors[layer].fill,
                  }}
                >
                  {layerColors[layer].label}
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
            className="absolute right-4 top-16 w-72 overflow-hidden rounded-lg border border-white/10 bg-black/80"
          >
            <div className="h-1" style={{ backgroundColor: getLayerColors(selectedNode.layer).fill }} />
            <div className="p-4">
              <div className="flex items-start justify-between mb-3">
                <div>
                  <h3 className="font-semibold text-white">{selectedNode.label}</h3>
                  <span
                    className="text-xs px-2 py-0.5 rounded-full capitalize"
                    style={{
                      backgroundColor: getLayerColors(selectedNode.layer).bg,
                      color: getLayerColors(selectedNode.layer).fill,
                    }}
                  >
                    {selectedNode.layer}
                  </span>
                </div>
                <button
                  onClick={() => setSelectedNodeId(null)}
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
                      onClick={() => setSelectedNodeId(node.id)}
                      className="text-xs px-2 py-1 rounded bg-white/5 hover:bg-white/10 transition-colors"
                      style={{ color: getLayerColors(node.layer).fill }}
                    >
                      {node.label}
                    </button>
                  ))}
                </div>
              </div>

              <div className="mt-4 pt-3 border-t border-white/10">
                <div className="flex items-center gap-2 mb-2">
                  <Link2 className="w-3 h-3 text-white/50" />
                  <span className="text-xs text-white/50">Relationship evidence</span>
                </div>
                {selectedEdges.length > 0 ? (
                  <div className="space-y-2">
                    {selectedEdges.map((edge) => {
                      const otherId = edge.source === selectedNode.id ? edge.target : edge.source
                      return (
                        <div key={`${edge.source}-${edge.target}-${edge.label || "relationship"}`} className="rounded-lg bg-white/5 p-2">
                          <div className="flex items-center justify-between gap-2">
                            <span className="truncate text-xs text-white/70">{getNodeLabel(otherId)}</span>
                            {edge.label && <span className="shrink-0 text-[10px] uppercase tracking-wide text-white/35">{edge.label}</span>}
                          </div>
                          <p className="mt-1 text-xs text-white/50">{describeEvidence(edge.evidence)}</p>
                          {edge.evidence?.reason && <p className="mt-1 text-xs text-white/40">{edge.evidence.reason}</p>}
                        </div>
                      )
                    })}
                  </div>
                ) : (
                  <p className="text-xs text-white/40">No relationship evidence recorded.</p>
                )}
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Legend */}
      <div className="absolute bottom-4 left-4 flex gap-4 px-4 py-2 rounded-lg bg-black/50 backdrop-blur-sm border border-white/10">
        {MEMORY_LAYERS.map((layer) => {
          const colors = layerColors[layer]
          return (
          <div key={layer} className="flex items-center gap-2">
            <div
              className="w-3 h-3 rounded-full"
              style={{ backgroundColor: colors.fill, boxShadow: `0 0 8px ${colors.glow}` }}
            />
            <span className="text-xs text-white/70">{colors.label}</span>
          </div>
          )
        })}
      </div>

      <div className="absolute bottom-16 left-4 text-xs text-white/40" role="status" data-testid="graph-settlement">
        {isSettled ? "Layout settled" : "Arranging layout"} · {filteredNodes.length} of {nodes.length} nodes visible
      </div>

      {/* Instructions */}
      <div className="absolute bottom-4 right-4 text-xs text-white/40 text-right">
        Scroll to zoom | Drag canvas to pan | Double-click node for details
      </div>
    </motion.div>
  )
}
