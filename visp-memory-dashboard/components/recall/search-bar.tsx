"use client"

import { useState, type FormEvent } from "react"
import { motion } from "framer-motion"
import { Search } from "lucide-react"
import { Button } from "@/components/ui/button"

interface SearchBarProps {
  onSearch: (query: string) => void
  isLoading?: boolean
}

export function SearchBar({ onSearch, isLoading }: SearchBarProps) {
  const [query, setQuery] = useState("")

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault()
    if (query.trim()) {
      onSearch(query.trim())
    }
  }

  return (
    <form onSubmit={handleSubmit}>
      <div className="surface rounded-2xl p-2 sm:p-3 flex items-center gap-2">
        <div className="flex items-center gap-3 min-w-0 flex-1 px-2 sm:px-3">
          <Search className="h-5 w-5 text-muted-foreground shrink-0" />
          <input
            type="search"
            aria-label="Search memories"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="What did I decide about authentication?"
            className="min-w-0 flex-1 bg-transparent border-none outline-none text-foreground placeholder:text-muted-foreground text-sm py-2"
          />
        </div>
        <motion.div whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}>
          <Button
            type="submit"
            disabled={isLoading || !query.trim()}
            className="px-4 sm:px-6"
          >
            {isLoading ? "Searching..." : "Search"}
          </Button>
        </motion.div>
      </div>
    </form>
  )
}
