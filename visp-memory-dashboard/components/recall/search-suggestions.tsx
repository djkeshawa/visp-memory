"use client"

interface SearchSuggestionsProps {
  onSelect: (suggestion: string) => void
}

const suggestions = ["authentication", "database schema", "API design"]

export function SearchSuggestions({ onSelect }: SearchSuggestionsProps) {
  return (
    <div className="surface rounded-2xl px-5 py-6 sm:px-6">
      <p className="eyebrow mb-3">Try searching for:</p>
      <div className="flex flex-wrap gap-2">
        {suggestions.map((suggestion) => (
          <button
            key={suggestion}
            type="button"
            onClick={() => onSelect(suggestion)}
            className="h-10 rounded-full border border-border px-4 text-sm text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
          >
            {suggestion}
          </button>
        ))}
      </div>
    </div>
  )
}
