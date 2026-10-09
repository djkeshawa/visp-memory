import { Search } from "lucide-react"
import { MEMORY_LAYERS, MEMORY_LAYER_CONFIG } from "@/lib/layers"
import type { MemoryLayer } from "@/lib/types"
import { cn } from "@/lib/utils"
import { LIBRARY_STATUSES, type LibraryStatus } from "./use-library"

export type LayerFilter = MemoryLayer | "all"

interface LibraryToolbarProps {
  status: LibraryStatus
  onStatusChange: (status: LibraryStatus) => void
  query: string
  onQueryChange: (query: string) => void
  layer: LayerFilter
  onLayerChange: (layer: LayerFilter) => void
}

const CONTROL = "h-11 rounded-[10px] border border-input bg-card text-sm"

/** Status switcher, a text filter and a layer filter. The filters apply to the loaded page. */
export function LibraryToolbar({ status, onStatusChange, query, onQueryChange, layer, onLayerChange }: LibraryToolbarProps) {
  return (
    <div className="flex flex-wrap items-center gap-2.5">
      <div role="group" aria-label="Memory status" className="flex flex-wrap gap-0.5 rounded-xl border border-border bg-card p-[3px]">
        {LIBRARY_STATUSES.map((item) => (
          <button
            key={item.value}
            type="button"
            aria-pressed={status === item.value}
            onClick={() => onStatusChange(item.value)}
            className={cn(
              "h-10 rounded-[9px] px-3 text-[13px] transition-colors",
              status === item.value ? "bg-secondary font-semibold text-foreground" : "text-muted-foreground hover:text-foreground",
            )}
          >
            {item.label}
          </button>
        ))}
      </div>
      <div className={cn(CONTROL, "flex min-w-0 flex-[1_1_15rem] items-center gap-2 px-3 focus-within:border-ring focus-within:ring-[3px] focus-within:ring-ring/30")}>
        <Search className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
        <label htmlFor="library-filter" className="sr-only">Filter memories</label>
        <input
          id="library-filter"
          type="search"
          value={query}
          onChange={(event) => onQueryChange(event.target.value)}
          placeholder="Filter by text, id, tag or file"
          className="min-w-0 flex-1 bg-transparent text-base outline-none placeholder:text-muted-foreground md:text-sm"
        />
      </div>
      <label htmlFor="library-layer" className="sr-only">Layer</label>
      <select
        id="library-layer"
        value={layer}
        onChange={(event) => onLayerChange(event.target.value as LayerFilter)}
        className={cn(CONTROL, "px-2.5")}
      >
        <option value="all">All layers</option>
        {MEMORY_LAYERS.map((item) => <option key={item} value={item}>{MEMORY_LAYER_CONFIG[item].label}</option>)}
      </select>
    </div>
  )
}
