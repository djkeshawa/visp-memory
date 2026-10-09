"use client"

import { useState } from "react"
import Link from "next/link"
import { ChevronDown, Library } from "lucide-react"
import { LayerGlyph } from "@/components/strata/layer-glyph"
import { MemoryMeta } from "@/components/strata/memory-meta"
import { MEMORY_LAYERS, MEMORY_LAYER_CONFIG } from "@/lib/layers"
import { projectHref, useSelectedProjectId } from "@/lib/project-selection"
import type { Memory, MemoryLayer } from "@/lib/types"
import { cn } from "@/lib/utils"

interface RecentActivityProps {
  memories: Memory[]
  isLoading?: boolean
  unavailable?: boolean
}

export function RecentActivity({ memories, isLoading, unavailable }: RecentActivityProps) {
  const selectedRepoId = useSelectedProjectId()
  const [layer, setLayer] = useState<MemoryLayer | "all">("all")
  const visible = memories.filter((memory) => layer === "all" || memory.layer === layer)
  // Raw captures are never recalled, so their chip appears only when the server returned some.
  const layers = MEMORY_LAYERS.filter((value) => value !== "raw" || memories.some((memory) => memory.layer === "raw"))

  return (
    <section className="surface min-w-0 overflow-hidden rounded-2xl" aria-labelledby="recent-heading" aria-busy={isLoading}>
      <div className="flex flex-wrap items-center justify-between gap-3 px-5 pb-3.5 pt-5 sm:px-6">
        <h2 id="recent-heading" className="text-[15px] font-semibold">Recently remembered</h2>
        <div className="flex flex-wrap gap-1" role="group" aria-label="Filter recent memories by layer">
          {(["all", ...layers] as const).map((value) => (
            <button key={value} type="button" aria-pressed={layer === value} onClick={() => setLayer(value)}
              className={cn("h-10 rounded-lg border px-3.5 text-[13px] transition-colors sm:h-8", layer === value ? "border-input bg-secondary font-medium text-foreground" : "border-transparent text-muted-foreground hover:bg-secondary/60")}>
              {value === "all" ? "All" : MEMORY_LAYER_CONFIG[value].label}
            </button>
          ))}
        </div>
      </div>
      {isLoading ? (
        <div role="status" className="space-y-6 border-t border-border p-6">
          <span className="sr-only">Loading recent memories</span>
          {[0, 1, 2].map((n) => <div key={n} className="space-y-3"><div className="shimmer h-4 w-4/5 rounded" /><div className="shimmer h-3 w-2/5 rounded" /></div>)}
        </div>
      ) : unavailable ? (
        <p className="border-t border-border p-6 text-sm text-muted-foreground">Recent memories are unavailable. Retry the connection above to load your library.</p>
      ) : visible.length === 0 ? (
        <div className="border-t border-border px-6 py-12 text-center">
          <Library className="mx-auto mb-4 h-7 w-7 text-muted-foreground" aria-hidden="true" />
          <h3 className="font-medium">{memories.length ? "No recent memories in this layer" : "Start with something worth remembering"}</h3>
          <p className="mx-auto mt-2 max-w-sm text-sm leading-6 text-muted-foreground">{memories.length ? "Choose another layer or open the library to explore older memories." : "Add a decision, a useful fact, or a lesson learned with New memory."}</p>
        </div>
      ) : (
        <div className="divide-y divide-border border-t border-border">
          {visible.map((memory) => (
            <details key={memory.id} className="group open:bg-secondary/30">
              <summary className="flex min-h-12 cursor-pointer list-none items-start gap-3.5 px-5 py-4 transition-colors hover:bg-secondary/50 sm:px-6 [&::-webkit-details-marker]:hidden">
                <LayerGlyph layer={memory.layer} className="mt-1" />
                <span className="flex min-w-0 flex-1 flex-col gap-2">
                  <span className="line-clamp-2 break-words text-[15px] leading-relaxed group-open:line-clamp-none group-open:whitespace-pre-wrap">{memory.content}</span>
                  <MemoryMeta memory={memory} />
                </span>
                <ChevronDown className="mt-1 h-4 w-4 shrink-0 text-muted-foreground transition-transform group-open:rotate-180" aria-hidden="true" />
              </summary>
              {(!!memory.tags?.length || !!memory.files?.length) && (
                <div className="space-y-3 px-5 pb-5 sm:pl-[3.375rem] sm:pr-6">
                  {!!memory.tags?.length && <div className="flex flex-wrap gap-2">{memory.tags.map((tag) => <span key={tag} className="rounded-full bg-secondary px-2.5 py-1 text-xs text-muted-foreground">{tag}</span>)}</div>}
                  {!!memory.files?.length && <p className="break-words font-mono text-xs text-muted-foreground">{memory.files.join(", ")}</p>}
                </div>
              )}
            </details>
          ))}
        </div>
      )}
      <div className="border-t border-border px-5 py-3.5 sm:px-6">
        <Link href={projectHref("/memories", selectedRepoId)} className="text-[13px] font-medium text-highlight hover:underline">Open the library<span aria-hidden="true"> →</span></Link>
      </div>
    </section>
  )
}
