"use client"

import { Suspense, useEffect, useMemo, useState } from "react"
import { AlertTriangle } from "lucide-react"
import { ConfidenceLegend, LayerChips, NodeSearch } from "@/components/graph/graph-controls"
import { GraphInspector } from "@/components/graph/graph-inspector"
import { MemoryGraph } from "@/components/graph/memory-graph"
import { useGraphData } from "@/components/graph/use-graph-data"
import { PageHeader } from "@/components/strata/primitives"
import { DEFAULT_GRAPH_LAYERS, MEMORY_LAYERS } from "@/lib/layers"
import { useSelectedProjectId } from "@/lib/project-selection"
import type { MemoryLayer } from "@/lib/types"

function GraphContent() {
  const repoId = useSelectedProjectId()
  const { graph, error, loading } = useGraphData(repoId)
  const [query, setQuery] = useState("")
  const [activeLayers, setActiveLayers] = useState<MemoryLayer[]>([...DEFAULT_GRAPH_LAYERS])
  const [selectedId, setSelectedId] = useState<string | null>(null)

  useEffect(() => setSelectedId(null), [graph])

  const counts = useMemo(() => {
    const result = Object.fromEntries(MEMORY_LAYERS.map((layer) => [layer, 0])) as Record<MemoryLayer, number>
    for (const node of graph?.nodes ?? []) result[node.layer] += 1
    return result
  }, [graph])

  const toggleLayer = (layer: MemoryLayer) =>
    setActiveLayers((current) => (current.includes(layer) ? current.filter((item) => item !== layer) : [...current, layer]))

  const summary = graph
    ? `${graph.nodes.length} nodes · ${graph.links.length} connections · line style shows how each link was established`
    : loading ? "Loading graph…" : "Graph unavailable"

  return (
    <div className="space-y-5">
      <PageHeader eyebrow={summary} title="Graph" actions={<NodeSearch value={query} onChange={setQuery} />} />

      <div className="flex flex-wrap items-center justify-between gap-2.5">
        <LayerChips counts={counts} active={activeLayers} onToggle={toggleLayer} />
        <ConfidenceLegend />
      </div>

      {error ? (
        <div role="alert" className="surface flex min-h-[22rem] items-center justify-center rounded-2xl p-6">
          <div className="max-w-md text-center text-sm text-muted-foreground">
            <AlertTriangle className="mx-auto mb-3 h-6 w-6 text-destructive" aria-hidden="true" />
            <p className="font-medium text-foreground">Graph data is unavailable</p>
            <p className="mt-2">{error}</p>
          </div>
        </div>
      ) : (
        <div className="flex flex-wrap items-start gap-[18px]">
          <MemoryGraph graph={graph} loading={loading} query={query} activeLayers={activeLayers} selectedId={selectedId} onSelect={setSelectedId} />
          <GraphInspector graph={graph} nodeId={selectedId} onSelect={setSelectedId} />
        </div>
      )}
    </div>
  )
}

export default function GraphPage() {
  return (
    <Suspense fallback={null}>
      <GraphContent />
    </Suspense>
  )
}
