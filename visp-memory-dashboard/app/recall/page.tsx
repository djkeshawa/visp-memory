"use client"

import { useEffect, useRef, useState, Suspense } from "react"
import { AlertTriangle } from "lucide-react"
import { Inspector } from "@/components/recall/inspector"
import { LayerFilter, type LayerChoice } from "@/components/recall/layer-filter"
import { SearchBar } from "@/components/recall/search-bar"
import { SearchResults } from "@/components/recall/search-results"
import { SearchSuggestions } from "@/components/recall/search-suggestions"
import { clearRecallQuery, peekRecallQuery } from "@/components/recall/recall-handoff"
import { PageHeader } from "@/components/strata/primitives"
import { describeApiError, searchMemories } from "@/lib/api"
import { isRecallableLayer } from "@/lib/layers"
import { useSelectedProjectId } from "@/lib/project-selection"
import type { SearchResult } from "@/lib/types"

function RecallContent() {
  const selectedRepoId = useSelectedProjectId()
  const [draft, setDraft] = useState("")
  const [query, setQuery] = useState("")
  const [results, setResults] = useState<SearchResult[] | null>(null)
  const [layer, setLayer] = useState<LayerChoice>("all")
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const selectedRepoIdRef = useRef(selectedRepoId)
  const requestGenerationRef = useRef(0)
  selectedRepoIdRef.current = selectedRepoId

  const handleSearch = async (searchQuery: string) => {
    const requestedRepoId = selectedRepoId
    const generation = ++requestGenerationRef.current
    setDraft(searchQuery)
    setQuery(searchQuery)
    setIsLoading(true)

    try {
      const searchResults = await searchMemories(searchQuery, 10, requestedRepoId)
      if (generation !== requestGenerationRef.current || selectedRepoIdRef.current !== requestedRepoId) return
      setResults(searchResults)
      setSelectedId(null)
      setErrorMessage(null)
    } catch (error) {
      if (generation !== requestGenerationRef.current || selectedRepoIdRef.current !== requestedRepoId) return
      console.error("Search failed:", error)
      setErrorMessage(describeApiError(error))
      setResults([])
    } finally {
      if (generation === requestGenerationRef.current && selectedRepoIdRef.current === requestedRepoId) {
        setIsLoading(false)
      }
    }
  }

  // Reset everything, including the layer filter, when the project changes; then run a query
  // handed over from the Desk. The entry is cleared on the next tick, not on read, because
  // Strict Mode runs this effect twice and the second run must still find it.
  const search = useRef(handleSearch)
  search.current = handleSearch
  useEffect(() => {
    ++requestGenerationRef.current
    setDraft("")
    setQuery("")
    setResults(null)
    setLayer("all")
    setSelectedId(null)
    setErrorMessage(null)
    setIsLoading(false)
    // The project resolves from the URL after the first render; wait for it so the
    // handed-over search runs in the right scope.
    const handedOver = selectedRepoId ? peekRecallQuery() : ""
    if (!handedOver) return
    void search.current(handedOver)
    const timer = window.setTimeout(clearRecallQuery, 0)
    return () => window.clearTimeout(timer)
  }, [selectedRepoId])

  // Recall is intentionally never a raw-layer browser. The API excludes raw rows too,
  // but keeping the guard at this display boundary prevents a misconfigured/older server
  // response from turning a recall result into an accidental raw-data disclosure.
  const recallable = (results || []).filter((memory) => isRecallableLayer(memory.layer))
  const shown = recallable.filter((memory) => layer === "all" || memory.layer === layer)
  const selected = shown.find((memory) => memory.id === selectedId) || shown[0] || null

  return (
    <div className="space-y-5">
      <PageHeader title="Recall" />
      <SearchBar value={draft} onChange={setDraft} onSearch={handleSearch} isLoading={isLoading} />

      {errorMessage ? (
        <div role="alert" className="flex items-start gap-2 rounded-xl border border-destructive/30 bg-destructive/5 p-4 text-sm text-muted-foreground">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" aria-hidden="true" />
          <span className="min-w-0 break-words">{errorMessage}</span>
        </div>
      ) : null}

      {results === null ? (
        <SearchSuggestions onSelect={handleSearch} />
      ) : (
        <>
          <div className="flex flex-wrap items-center justify-between gap-2.5">
            <LayerFilter value={layer} onChange={setLayer} />
            <span role="status" className="text-[13px] text-muted-foreground">
              {shown.length} result{shown.length !== 1 && "s"} · raw captures are never shown in recall
            </span>
          </div>
          {selected ? (
            <div className="flex flex-wrap items-start gap-5">
              <div className="min-w-0 flex-[999_1_440px]">
                <SearchResults results={shown} selectedId={selected.id} onSelect={setSelectedId} />
              </div>
              <Inspector memory={selected} repoId={selectedRepoId} />
            </div>
          ) : (
            <div className="surface rounded-2xl p-8 text-center">
              <p className="text-muted-foreground">
                {recallable.length
                  ? "No results in this layer. Choose another layer to see the rest."
                  : <>No results found for &quot;<span className="font-medium text-foreground">{query}</span>&quot;</>}
              </p>
            </div>
          )}
        </>
      )}
    </div>
  )
}

export default function RecallPage() {
  return (
    <Suspense fallback={null}>
      <RecallContent />
    </Suspense>
  )
}
