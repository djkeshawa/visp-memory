"use client"

import { Suspense, useEffect, useState } from "react"
import { motion } from "framer-motion"
import { AlertTriangle, Database, Target, Brain, Share2 } from "lucide-react"
import { AnimatedStatsCard } from "@/components/dashboard/animated-stats-card"
import { RecentActivity } from "@/components/dashboard/recent-activity"
import { QuickActions } from "@/components/dashboard/quick-actions"
import { SystemStatus } from "@/components/dashboard/system-status"
import { describeApiError, getStats, getRecentMemories } from "@/lib/api"
import { mockSystemStatus } from "@/lib/mock-data"
import { pageTransition } from "@/lib/animations"
import { useSelectedProjectId } from "@/lib/project-selection"
import type { Memory, Stats } from "@/lib/types"

function DashboardContent() {
  const selectedRepoId = useSelectedProjectId()
  const [stats, setStats] = useState<Stats | null>(null)
  const [memories, setMemories] = useState<Memory[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)

  useEffect(() => {
    async function fetchData() {
      setIsLoading(true)
      try {
        const [statsData, memoriesData] = await Promise.all([
          getStats(selectedRepoId),
          getRecentMemories(8, selectedRepoId),
        ])
        setStats(statsData)
        setMemories(memoriesData)
        setLoadError(null)
      } catch (error) {
        console.error("Failed to fetch dashboard data:", error)
        setLoadError(describeLoadError(error))
      } finally {
        setIsLoading(false)
      }
    }
    fetchData()
  }, [selectedRepoId])

  const displayStats = stats || {
    totalMemories: 0,
    activeIntents: 0,
    knowledgeNodes: 0,
    connections: 0,
  }

  return (
    <motion.div initial="initial" animate="animate" variants={pageTransition} className="space-y-8">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-semibold text-foreground">Welcome back</h1>
        <p className="text-muted-foreground mt-1">
          {selectedRepoId ? `Project: ${selectedRepoId}` : "Here's an overview of your memory system"}
        </p>
      </div>

      {loadError ? (
        <div className="glass rounded-xl border border-destructive/30 p-4">
          <div className="flex items-start gap-3">
            <AlertTriangle className="mt-0.5 h-5 w-5 text-destructive" />
            <div>
              <p className="text-sm font-medium text-foreground">Dashboard is not connected</p>
              <p className="mt-1 text-sm text-muted-foreground">{loadError}</p>
            </div>
          </div>
        </div>
      ) : null}

      {/* Stats Grid */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <AnimatedStatsCard
          title="Total Memories"
          value={displayStats.totalMemories}
          icon={<Database className="h-5 w-5 text-white" />}
          change={isLoading ? "Loading..." : "Total stored"}
          gradient="bg-gradient-to-br from-blue-500 to-blue-600"
        />
        <AnimatedStatsCard
          title="Active Intents"
          value={displayStats.activeIntents}
          icon={<Target className="h-5 w-5 text-white" />}
          change={isLoading ? "Loading..." : "In progress"}
          gradient="bg-gradient-to-br from-amber-500 to-amber-600"
        />
        <AnimatedStatsCard
          title="Knowledge Nodes"
          value={displayStats.knowledgeNodes}
          icon={<Brain className="h-5 w-5 text-white" />}
          change={isLoading ? "Loading..." : "Semantic layer"}
          gradient="bg-gradient-to-br from-purple-500 to-purple-600"
        />
        <AnimatedStatsCard
          title="Connections"
          value={displayStats.connections}
          icon={<Share2 className="h-5 w-5 text-white" />}
          change={isLoading ? "Loading..." : "Graph edges"}
          gradient="bg-gradient-to-br from-cyan-500 to-cyan-600"
        />
      </div>

      {/* Content Grid */}
      <div className="grid gap-6 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <RecentActivity memories={memories} />
        </div>
        <div className="space-y-6">
          <QuickActions />
          <SystemStatus status={mockSystemStatus} />
        </div>
      </div>
    </motion.div>
  )
}

export default function DashboardPage() {
  return (
    <Suspense fallback={null}>
      <DashboardContent />
    </Suspense>
  )
}

function describeLoadError(error: unknown): string {
  return describeApiError(error)
}
