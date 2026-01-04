"use client"

import { motion } from "framer-motion"

interface SearchSuggestionsProps {
  onSelect: (suggestion: string) => void
}

const suggestions = ["authentication", "database schema", "API design"]

export function SearchSuggestions({ onSelect }: SearchSuggestionsProps) {
  return (
    <div className="glass rounded-xl p-8 text-center">
      <p className="text-muted-foreground mb-4">Try searching for:</p>
      <div className="flex items-center justify-center gap-3 flex-wrap">
        {suggestions.map((suggestion) => (
          <motion.button
            key={suggestion}
            onClick={() => onSelect(suggestion)}
            whileHover={{ scale: 1.05 }}
            whileTap={{ scale: 0.95 }}
            className="px-4 py-2 rounded-full bg-secondary text-secondary-foreground text-sm font-medium hover:bg-primary hover:text-primary-foreground transition-colors"
          >
            {suggestion}
          </motion.button>
        ))}
      </div>
    </div>
  )
}
