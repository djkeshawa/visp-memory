"use client"

import { useEffect, useState } from "react"
import { motion } from "framer-motion"
import { MemoryGraph } from "@/components/graph/memory-graph"
import { pageTransition } from "@/lib/animations"
import { Network } from "lucide-react"
import { getStats } from "@/lib/api"
import type { Stats } from "@/lib/types"

export default function GraphPage() {
  const [stats, setStats] = useState<Stats | null>(null)

  useEffect(() => {
    getStats().then(setStats).catch(console.error)
  }, [])

  return (
    <motion.div initial="initial" animate="animate" variants={pageTransition} className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-foreground flex items-center gap-3">
            <Network className="w-6 h-6 text-muted-foreground" />
            Memory Graph
          </h1>
          <p className="text-muted-foreground mt-1">
            Explore connections between your memories in an interactive knowledge map
          </p>
        </div>
        <div className="flex gap-6 text-sm">
          <div className="text-center">
            <div className="text-2xl font-semibold text-foreground">{stats?.totalMemories || "-"}</div>
            <div className="text-muted-foreground">Nodes</div>
          </div>
          <div className="text-center">
            <div className="text-2xl font-semibold text-foreground">{stats?.connections || "-"}</div>
            <div className="text-muted-foreground">Connections</div>
          </div>
          <div className="text-center">
            <div className="text-2xl font-semibold text-foreground">3</div>
            <div className="text-muted-foreground">Layers</div>
          </div>
        </div>
      </div>

      <MemoryGraph />
    </motion.div>
  )
}
