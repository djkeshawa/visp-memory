"use client"

import { Suspense, useEffect, useRef, useState } from "react"
import { PageHeader } from "@/components/strata/primitives"
import { describeApiError, getDecayPreview } from "@/lib/api"
import { useSelectedProjectId } from "@/lib/project-selection"
import type { DecayPreviewResponse } from "@/lib/types"
import { DecayPreview } from "./_components/decay-preview"
import { HealthStats } from "./_components/health-stats"
import { RuntimeCards } from "./_components/runtime-cards"

function HealthContent() {
  const selectedRepoId = useSelectedProjectId()
  const [preview, setPreview] = useState<DecayPreviewResponse | null>(null)
  const [halflifeDays, setHalflifeDays] = useState(30)
  const [limit, setLimit] = useState(25)
  const [isLoading, setIsLoading] = useState(true)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const selectedRepoIdRef = useRef(selectedRepoId)
  const requestGenerationRef = useRef(0)
  selectedRepoIdRef.current = selectedRepoId

  const loadPreview = async () => {
    const requestedRepoId = selectedRepoId
    const generation = ++requestGenerationRef.current
    setIsLoading(true)
    try {
      const data = await getDecayPreview({ repoId: requestedRepoId, limit, halflifeDays, minImportance: 0.1 })
      if (generation !== requestGenerationRef.current || selectedRepoIdRef.current !== requestedRepoId) return
      setPreview(data)
      setErrorMessage(null)
    } catch (error) {
      if (generation !== requestGenerationRef.current || selectedRepoIdRef.current !== requestedRepoId) return
      console.error("Failed to load memory health", error)
      setErrorMessage(describeApiError(error))
    } finally {
      if (generation === requestGenerationRef.current && selectedRepoIdRef.current === requestedRepoId) {
        setIsLoading(false)
      }
    }
  }

  useEffect(() => {
    setPreview(null)
    setErrorMessage(null)
    void loadPreview()
  }, [selectedRepoId])

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        eyebrow={selectedRepoId ? `Runtime, strength and decay · project ${selectedRepoId}` : "Runtime, strength and decay"}
        title="Operations"
      />

      <RuntimeCards />

      {errorMessage ? <p className="rounded-xl border border-destructive/30 bg-destructive/10 p-4 text-sm text-destructive" role="alert">{errorMessage}</p> : null}

      <HealthStats candidates={preview?.candidates ?? []} />

      <DecayPreview
        preview={preview}
        halflifeDays={halflifeDays}
        limit={limit}
        isLoading={isLoading}
        onHalflife={setHalflifeDays}
        onLimit={setLimit}
        onPreview={() => void loadPreview()}
      />
    </div>
  )
}

export default function HealthPage() {
  return (
    <Suspense fallback={null}>
      <HealthContent />
    </Suspense>
  )
}
