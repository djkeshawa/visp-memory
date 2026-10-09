"use client"

import { useEffect, useState } from "react"
import { describeApiError, getGraphData } from "@/lib/api"
import type { GraphData } from "@/lib/types"

interface GraphState {
  repoId: string | null | undefined
  graph: GraphData | null
  error: string | null
}

/**
 * Loads the graph for a project. The result is stored with the project it was requested for,
 * so switching projects clears the previous graph at once and a late response for the old
 * project is discarded.
 */
export function useGraphData(repoId: string | null | undefined) {
  const [state, setState] = useState<GraphState | null>(null)

  useEffect(() => {
    let cancelled = false
    getGraphData(repoId)
      .then((graph) => { if (!cancelled) setState({ repoId, graph, error: null }) })
      .catch((error) => {
        if (cancelled) return
        console.error("Failed to fetch graph data:", error)
        setState({ repoId, graph: null, error: describeApiError(error) })
      })
    return () => { cancelled = true }
  }, [repoId])

  const current = state && state.repoId === repoId ? state : null
  return { graph: current?.graph ?? null, error: current?.error ?? null, loading: current === null }
}
