"use client"

import { useEffect, useState } from "react"
import { RefreshCw, Undo2 } from "lucide-react"
import { PageHeader } from "@/components/strata/primitives"
import { Button } from "@/components/ui/button"
import { memoryMatchesQuery } from "./_components/filter"
import { LibraryToolbar, type LayerFilter } from "./_components/library-toolbar"
import { MemoryTable } from "./_components/memory-table"
import { MergeDialog } from "./_components/merge-dialog"
import { SelectionBar } from "./_components/selection-bar"
import { LIBRARY_STATUSES, PAGE_SIZE, useLibrary } from "./_components/use-library"

export default function MemoriesPage() {
  const library = useLibrary()
  const { status, memories, selected, loading, error, pageIndex, hasNextPage } = library
  const [query, setQuery] = useState("")
  const [layer, setLayer] = useState<LayerFilter>("all")
  const statusLabel = LIBRARY_STATUSES.find((item) => item.value === status)?.label.toLowerCase() ?? status
  const filtering = query.trim() !== "" || layer !== "all"
  const visible = memories.filter((memory) => (layer === "all" || memory.layer === layer) && memoryMatchesQuery(memory, query))
  const firstIndex = pageIndex * PAGE_SIZE + 1
  const { keepOnly } = library
  const visibleKey = visible.map((memory) => memory.id).join("\n")

  // A filter that hides a selected row also deselects it: Merge and Move to trash act only on what is shown.
  useEffect(() => {
    keepOnly(new Set(visibleKey ? visibleKey.split("\n") : []))
  }, [keepOnly, visibleKey])

  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow="Content, provenance, duplicates and lifecycle"
        title="Library"
        actions={
          <>
            {library.lastOperation ? <Button variant="outline" className="h-11" onClick={() => void library.undo()}><Undo2 aria-hidden="true" />Undo merge</Button> : null}
            <Button variant="outline" className="h-11" onClick={() => void library.reload()} disabled={loading}>
              <RefreshCw aria-hidden="true" className={loading ? "animate-spin" : undefined} />Refresh
            </Button>
          </>
        }
      />

      <LibraryToolbar status={status} onStatusChange={library.setStatus} query={query} onQueryChange={setQuery} layer={layer} onLayerChange={setLayer} />

      {status === "active" ? <SelectionBar count={selected.length} busy={library.trashing} onMerge={library.merge.openMerge} onTrash={() => void library.removeSelected()} /> : null}

      {error ? <p className="rounded-xl border border-destructive/30 bg-destructive/12 p-3 text-sm text-destructive" role="alert">{error}</p> : null}

      {visible.length ? <MemoryTable
        memories={visible}
        status={status}
        selected={selected}
        onToggle={library.toggle}
        onTrash={(memory) => void library.remove(memory)}
        onRestore={(memory) => void library.restore(memory)}
        onPurge={(memory) => void library.purge(memory)}
      /> : null}
      {!loading && memories.length === 0 ? (
        <p className="surface rounded-2xl p-8 text-center text-sm text-muted-foreground">No {statusLabel} memories in this project.</p>
      ) : !loading && filtering && visible.length === 0 ? (
        <p className="surface rounded-2xl p-8 text-center text-sm text-muted-foreground">No memories on this page match the filter.</p>
      ) : null}

      <nav aria-label="Memory pagination" className="flex flex-wrap items-center justify-between gap-3">
        <p role="status" className="text-[13px] text-muted-foreground">
          {loading
            ? "Loading memories…"
            : memories.length
              ? `Page ${pageIndex + 1} · Memories ${firstIndex}–${firstIndex + memories.length - 1}${filtering ? ` · ${visible.length} match the filter on this page` : ""}`
              : "No memories to display"}
        </p>
        <div className="flex gap-2">
          <Button variant="outline" disabled={loading || pageIndex === 0} onClick={() => library.goToPage(pageIndex - 1)}>Previous page</Button>
          <Button variant="outline" disabled={loading || !hasNextPage} onClick={() => library.goToPage(pageIndex + 1)}>Next page</Button>
        </div>
      </nav>

      <MergeDialog library={library} selected={selected} />
    </div>
  )
}
