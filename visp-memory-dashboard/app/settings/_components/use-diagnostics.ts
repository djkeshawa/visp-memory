"use client"

import { useEffect, useRef, useState } from "react"
import {
  describeApiError,
  getEmbeddingIndexStatus,
  getModelRoutingStatus,
  getProviderDiagnostics,
  getStorageDiagnostics,
  reindexEmbeddingIndex,
  testModelRouting,
  testProvider,
} from "@/lib/api"
import { useSelectedProjectId } from "@/lib/project-selection"
import type {
  EmbeddingIndexStatus,
  EmbeddingReindexResult,
  ModelRoutingStatus,
  ProviderDiagnostic,
  StorageDiagnostics,
} from "@/lib/types"
import { upsertProvider } from "./format"

/** Loads and mutates the diagnostics shown on Settings, guarding against stale project responses. */
export function useDiagnostics() {
  const selectedRepoId = useSelectedProjectId()
  const [providers, setProviders] = useState<ProviderDiagnostic[]>([])
  const [embeddingIndex, setEmbeddingIndex] = useState<EmbeddingIndexStatus | null>(null)
  const [storageDiagnostics, setStorageDiagnostics] = useState<StorageDiagnostics | null>(null)
  const [modelRouting, setModelRouting] = useState<ModelRoutingStatus | null>(null)
  const [reindexResult, setReindexResult] = useState<EmbeddingReindexResult | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [testingProvider, setTestingProvider] = useState<string | null>(null)
  const [isReindexing, setIsReindexing] = useState(false)
  const [isTestingModel, setIsTestingModel] = useState(false)
  const [modelMessage, setModelMessage] = useState<string | null>(null)
  const selectedRepoIdRef = useRef(selectedRepoId)
  const diagnosticsGenerationRef = useRef(0)
  const reindexGenerationRef = useRef(0)
  selectedRepoIdRef.current = selectedRepoId

  useEffect(() => {
    setEmbeddingIndex(null)
    setReindexResult(null)
    setLoadError(null)
    setIsReindexing(false)
    void loadDiagnostics()
  }, [selectedRepoId])

  const loadDiagnostics = async () => {
    const requestedRepoId = selectedRepoId
    const generation = ++diagnosticsGenerationRef.current
    setIsLoading(true)
    try {
      const [providerData, indexData, storageData, modelData] = await Promise.all([
        getProviderDiagnostics(),
        getEmbeddingIndexStatus(requestedRepoId),
        getStorageDiagnostics(),
        getModelRoutingStatus(),
      ])
      if (generation !== diagnosticsGenerationRef.current || selectedRepoIdRef.current !== requestedRepoId) return
      setProviders(providerData)
      setEmbeddingIndex(indexData)
      setStorageDiagnostics(storageData)
      setModelRouting(modelData)
      setLoadError(null)
    } catch (error) {
      if (generation !== diagnosticsGenerationRef.current || selectedRepoIdRef.current !== requestedRepoId) return
      console.error("Failed to fetch diagnostics:", error)
      setLoadError(describeApiError(error))
    } finally {
      if (generation === diagnosticsGenerationRef.current && selectedRepoIdRef.current === requestedRepoId) {
        setIsLoading(false)
      }
    }
  }

  const handleReindex = async (dryRun: boolean) => {
    const requestedRepoId = selectedRepoId
    const generation = ++reindexGenerationRef.current
    setIsReindexing(true)
    try {
      const result = await reindexEmbeddingIndex({ repoId: requestedRepoId, dryRun })
      if (generation !== reindexGenerationRef.current || selectedRepoIdRef.current !== requestedRepoId) return
      setReindexResult(result)
      const updatedIndex = await getEmbeddingIndexStatus(requestedRepoId)
      if (generation !== reindexGenerationRef.current || selectedRepoIdRef.current !== requestedRepoId) return
      setEmbeddingIndex(updatedIndex)
      setLoadError(null)
    } catch (error) {
      if (generation !== reindexGenerationRef.current || selectedRepoIdRef.current !== requestedRepoId) return
      setLoadError(describeApiError(error))
    } finally {
      if (generation === reindexGenerationRef.current && selectedRepoIdRef.current === requestedRepoId) {
        setIsReindexing(false)
      }
    }
  }

  const handleTest = async (providerName: string) => {
    setTestingProvider(providerName)
    try {
      const result = await testProvider(providerName)
      setProviders((current) => upsertProvider(current, result))
    } catch (error) {
      setProviders((current) =>
        upsertProvider(current, {
          provider: providerName,
          status: "failed",
          message: describeApiError(error),
          lastChecked: new Date().toISOString(),
        }),
      )
    } finally {
      setTestingProvider(null)
    }
  }

  const handleModelTest = async () => {
    setIsTestingModel(true)
    try {
      await testModelRouting()
      setModelMessage("Task model connected successfully.")
    } catch (error) {
      setModelMessage(describeApiError(error))
    } finally {
      setIsTestingModel(false)
    }
  }

  return {
    providers, embeddingIndex, storageDiagnostics, modelRouting, reindexResult,
    isLoading, loadError, testingProvider, isReindexing, isTestingModel, modelMessage,
    loadDiagnostics, handleReindex, handleTest, handleModelTest,
  }
}
