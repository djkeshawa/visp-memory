import { LayerGlyph } from "@/components/strata/layer-glyph"
import { MemoryMeta } from "@/components/strata/memory-meta"
import type { SearchResult } from "@/lib/types"
import { cn } from "@/lib/utils"

interface SearchResultsProps {
  results: SearchResult[]
  selectedId: string | null
  onSelect: (id: string) => void
}

/** A bounded 0..1 ranking score. It orders results; it is never shown as a probability. */
function ScoreBar({ score }: { score: number }) {
  return (
    <span className="flex shrink-0 flex-col items-end gap-1.5">
      <span className="font-mono text-[13px]"><span className="sr-only">Ranking score </span>{score.toFixed(2)}</span>
      <span aria-hidden="true" className="block h-1 w-14 overflow-hidden rounded-sm bg-border">
        <span className="block h-full bg-highlight" style={{ width: `${Math.round(Math.min(Math.max(score, 0), 1) * 100)}%` }} />
      </span>
    </span>
  )
}

export function SearchResults({ results, selectedId, onSelect }: SearchResultsProps) {
  return (
    <section aria-label="Results" className="flex min-w-0 flex-col gap-2">
      {results.map((memory) => (
        <button
          key={memory.id}
          type="button"
          aria-pressed={memory.id === selectedId}
          onClick={() => onSelect(memory.id)}
          className={cn(
            "flex w-full gap-3.5 rounded-[14px] border px-4 py-4 text-left transition-colors sm:px-[18px]",
            memory.id === selectedId ? "border-ring bg-secondary" : "border-border bg-card hover:bg-secondary/60",
          )}
        >
          <LayerGlyph layer={memory.layer} className="mt-1" />
          <span className="flex min-w-0 flex-1 flex-col gap-2">
            <span className="break-words text-[15px] leading-relaxed">{memory.content}</span>
            <MemoryMeta memory={memory} />
          </span>
          {memory.relevanceScore !== undefined ? <ScoreBar score={memory.relevanceScore} /> : null}
        </button>
      ))}
    </section>
  )
}
