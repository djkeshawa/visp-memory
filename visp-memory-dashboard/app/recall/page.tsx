"use client"

import { useEffect, useRef, useState, Suspense } from "react"
import { motion } from "framer-motion"
import { AlertTriangle, Search } from "lucide-react"
import { SearchBar } from "@/components/recall/search-bar"
import { SearchSuggestions } from "@/components/recall/search-suggestions"
import { SearchResults } from "@/components/recall/search-results"
import { describeApiError, searchMemories } from "@/lib/api"
import { pageTransition } from "@/lib/animations"
import { useSelectedProjectId } from "@/lib/project-selection"
import type { Memory } from "@/lib/types"

function RecallContent() {
  const selectedRepoId = useSelectedProjectId()
  const [query, setQuery] = useState("")
  const [results, setResults] = useState<Memory[] | null>(null)
  const [isLoading, setIsLoading] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const selectedRepoIdRef = useRef(selectedRepoId)
  const requestGenerationRef = useRef(0)
  selectedRepoIdRef.current = selectedRepoId

  useEffect(() => {
    ++requestGenerationRef.current
    setQuery("")
    setResults(null)
    setErrorMessage(null)
    setIsLoading(false)
  }, [selectedRepoId])

  const handleSearch = async (searchQuery: string) => {
    const requestedRepoId = selectedRepoId
    const generation = ++requestGenerationRef.current
    setQuery(searchQuery)
    setIsLoading(true)

    try {
      const searchResults = await searchMemories(searchQuery, 10, requestedRepoId)
      if (generation !== requestGenerationRef.current || selectedRepoIdRef.current !== requestedRepoId) return
      setResults(searchResults)
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

  return (
    <motion.div initial="initial" animate="animate" variants={pageTransition} className="max-w-2xl mx-auto space-y-8">
      {/* Header */}
      <div className="text-center space-y-4">
        <motion.div
          whileHover={{ rotate: 5, scale: 1.1 }}
          className="mx-auto inline-flex h-12 w-12 items-center justify-center rounded-md bg-primary text-primary-foreground"
        >
          <Search className="h-6 w-6" />
        </motion.div>
        <div>
          <h1 className="text-2xl font-semibold text-foreground">Recall</h1>
          <p className="text-muted-foreground mt-1">Search your memories using natural language</p>
        </div>
      </div>

      {/* Search Bar */}
      <SearchBar onSearch={handleSearch} isLoading={isLoading} />

      {errorMessage ? (
        <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-4 text-sm text-muted-foreground">
          <div className="flex items-start gap-2">
            <AlertTriangle className="mt-0.5 h-4 w-4 text-destructive" />
            <span>{errorMessage}</span>
          </div>
        </div>
      ) : null}

      {/* Results or Suggestions */}
      {results !== null ? (
        <SearchResults results={results} query={query} />
      ) : (
        <SearchSuggestions onSelect={handleSearch} />
      )}
    </motion.div>
  )
}

export default function RecallPage() {
  return (
    <Suspense fallback={null}>
      <RecallContent />
    </Suspense>
  )
}
