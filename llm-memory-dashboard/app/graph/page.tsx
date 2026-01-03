"use client"

import { motion } from "framer-motion"
import { MemoryGraph } from "@/components/graph/memory-graph"
import { GraphLegend } from "@/components/graph/graph-legend"
import { pageTransition } from "@/lib/animations"

export default function GraphPage() {
  return (
    <motion.div initial="initial" animate="animate" variants={pageTransition} className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-foreground">Memory Graph</h1>
          <p className="text-muted-foreground mt-1">Visualize connections between your memories</p>
        </div>
        <GraphLegend />
      </div>

      {/* Graph Container */}
      <MemoryGraph />
    </motion.div>
  )
}
