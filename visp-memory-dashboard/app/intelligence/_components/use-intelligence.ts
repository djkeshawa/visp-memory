"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { describeApiError, getDecayPreview, getDuplicateCandidates, getGraphData, getMemoryIntelligenceReport } from "@/lib/api"
import { useSelectedProjectId } from "@/lib/project-selection"
import type { DecayPreviewItem, DuplicateCandidate, GraphLink, MemoryIntelligenceReport } from "@/lib/types"

export const DECAY_HALFLIFE_DAYS = 30

function hasRelationshipEvidence(link: GraphLink): boolean {
  const evidence = link.evidence
  return Boolean(evidence?.reason || evidence?.confidence || evidence?.source)
}

/**
 * Loads the report and the three supporting lists for the selected project. A response that
 * arrives after the project changed, or after a newer load started, is discarded.
 */
export function useIntelligence() {
  const repoId = useSelectedProjectId()
  const [report, setReport] = useState<MemoryIntelligenceReport | null>(null)
  const [graphEvidence, setGraphEvidence] = useState<GraphLink[]>([])
  const [duplicates, setDuplicates] = useState<DuplicateCandidate[]>([])
  const [freshness, setFreshness] = useState<DecayPreviewItem[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const repoIdRef = useRef(repoId)
  const generationRef = useRef(0)
  repoIdRef.current = repoId

  const load = useCallback(async () => {
    const requestedRepoId = repoId
    const generation = ++generationRef.current
    const isCurrent = () => generation === generationRef.current && repoIdRef.current === requestedRepoId
    setIsLoading(true)
    try {
      const [reportData, graphData, duplicateData, freshnessData] = await Promise.all([
        getMemoryIntelligenceReport(requestedRepoId, 8),
        getGraphData(requestedRepoId),
        getDuplicateCandidates({ repoId: requestedRepoId, limit: 8 }),
        getDecayPreview({ repoId: requestedRepoId, limit: 8, halflifeDays: DECAY_HALFLIFE_DAYS, minImportance: 0.1 }),
      ])
      if (!isCurrent()) return
      setReport(reportData)
      setGraphEvidence(graphData.links.filter(hasRelationshipEvidence))
      setDuplicates(duplicateData.candidates)
      setFreshness(freshnessData.candidates.filter((item) => item.risk !== "stable"))
      setErrorMessage(null)
    } catch (error) {
      if (!isCurrent()) return
      console.error("Failed to load memory intelligence", error)
      setErrorMessage(describeApiError(error))
    } finally {
      if (isCurrent()) setIsLoading(false)
    }
  }, [repoId])

  useEffect(() => {
    setReport(null)
    setGraphEvidence([])
    setDuplicates([])
    setFreshness([])
    setErrorMessage(null)
    void load()
  }, [load])

  return { repoId, report, graphEvidence, duplicates, freshness, isLoading, errorMessage, reload: load }
}
