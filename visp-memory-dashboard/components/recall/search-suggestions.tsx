"use client"

import { motion } from "framer-motion"

interface SearchSuggestionsProps {
  onSelect: (suggestion: string) => void
}

const suggestions = ["authentication", "database schema", "API design"]

export function SearchSuggestions({ onSelect }: SearchSuggestionsProps) {
  return (
    <div className="px-2 py-4 text-center">
      <p className="text-xs text-muted-foreground mb-4">Try searching for:</p>
      <div className="flex items-center justify-center gap-3 flex-wrap">
        {suggestions.map((suggestion) => (
          <motion.button
            key={suggestion}
            onClick={() => onSelect(suggestion)}


            className="px-4 py-2 rounded-full border border-border bg-card text-muted-foreground text-xs font-medium hover:bg-accent hover:text-accent-foreground transition-colors"
          >
            {suggestion}
          </motion.button>
        ))}
      </div>
    </div>
  )
}
