
"use client"

import { useMemo, useEffect } from "react"
import {
  ReactFlow,
  Background,
  Controls,
  useNodesState,
  useEdgesState,
  type Node,
  type Edge,
  type NodeTypes,
  Handle,
  Position,
} from "@xyflow/react"
import "@xyflow/react/dist/style.css"
import { cn } from "@/lib/utils"
import type { MemoryLayer } from "@/lib/types"
import { getGraphData } from "@/lib/api"

interface MemoryNodeData extends Record<string, unknown> {
  label: string
  layer: MemoryLayer
}

const layerColors: Record<MemoryLayer, string> = {
  episodic: "from-blue-500 to-blue-600",
  semantic: "from-purple-500 to-purple-600",
  intent: "from-amber-500 to-amber-600",
}

function MemoryNode({ data }: { data: MemoryNodeData }) {
  return (
    <div
      className={cn(
        "px-4 py-3 rounded-xl shadow-lg bg-gradient-to-br text-white text-sm font-medium max-w-[180px] text-center",
        layerColors[data.layer] || layerColors.episodic,
      )}
    >
      <Handle type="target" position={Position.Top} className="!bg-white/50 !w-2 !h-2" />
      <span className="line-clamp-2">{data.label}</span>
      <Handle type="source" position={Position.Bottom} className="!bg-white/50 !w-2 !h-2" />
    </div>
  )
}

const nodeTypes: NodeTypes = {
  memory: MemoryNode,
}

export function MemoryGraph() {
  const [nodes, setNodes, onNodesChange] = useNodesState<Node<MemoryNodeData>>([])
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([])

  useEffect(() => {
    async function fetchGraph() {
      try {
        const data = await getGraphData()

        // Transform API nodes to React Flow nodes
        const flowNodes: Node<MemoryNodeData>[] = data.nodes.map((node: any, index: number) => ({
          id: node.id,
          // Simple layout algorithm (random but distributed) could be improved
          position: {
            x: (index % 5) * 200 + Math.random() * 50,
            y: Math.floor(index / 5) * 150 + Math.random() * 50
          },
          data: { label: node.label, layer: node.group },
          type: "memory"
        }))

        // Transform API links to React Flow edges
        const flowEdges: Edge[] = data.links.map((link: any, i: number) => ({
          id: `e-${i}`,
          source: link.source,
          target: link.target,
          animated: true,
          style: { stroke: 'hsl(var(--foreground))', strokeWidth: 2 },
        }))

        setNodes(flowNodes)
        setEdges(flowEdges)
      } catch (e) {
        console.error("Failed to load graph", e)
      }
    }
    fetchGraph()
  }, [setNodes, setEdges])

  const proOptions = useMemo(() => ({ hideAttribution: true }), [])

  return (
    <div className="glass rounded-2xl overflow-hidden h-[500px]">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        nodeTypes={nodeTypes}
        proOptions={proOptions}
        fitView
        className="bg-background"
      >
        <Background color="hsl(var(--muted-foreground))" gap={20} size={1} />
        <Controls className="!bg-card !border-border !shadow-lg [&>button]:!bg-card [&>button]:!border-border [&>button]:!text-foreground [&>button:hover]:!bg-secondary" />
      </ReactFlow>
    </div>
  )
}
