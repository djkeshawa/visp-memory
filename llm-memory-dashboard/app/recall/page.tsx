"use client"

import { useState, Suspense } from "react"
import { motion } from "framer-motion"
import { AlertTriangle, Search } from "lucide-react"
import { SearchBar } from "@/components/recall/search-bar"
import { SearchSuggestions } from "@/components/recall/search-suggestions"
import { SearchResults } from "@/components/recall/search-results"
import { describeApiError, searchMemories } from "@/lib/api"
import { pageTransition } from "@/lib/animations"
import type { Memory } from "@/lib/types"

function RecallContent() {
  const [query, setQuery] = useState("")
  const [results, setResults] = useState<Memory[] | null>(null)
  const [isLoading, setIsLoading] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  const handleSearch = async (searchQuery: string) => {
    setQuery(searchQuery)
    setIsLoading(true)

    try {
      const searchResults = await searchMemories(searchQuery)
      setResults(searchResults)
      setErrorMessage(null)
    } catch (error) {
      console.error("Search failed:", error)
      setErrorMessage(describeApiError(error))
      setResults([])
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <motion.div initial="initial" animate="animate" variants={pageTransition} className="max-w-2xl mx-auto space-y-8">
      {/* Header */}
      <div className="text-center space-y-4">
        <motion.div
          whileHover={{ rotate: 5, scale: 1.1 }}
          className="inline-flex h-16 w-16 items-center justify-center rounded-2xl bg-gradient-to-br from-blue-500 to-indigo-600 shadow-lg mx-auto"
        >
          <Search className="h-8 w-8 text-white" />
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
