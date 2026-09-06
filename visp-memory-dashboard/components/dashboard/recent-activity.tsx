"use client"

import { useState } from "react"
import Link from "next/link"
import { ChevronDown, Library, SlidersHorizontal } from "lucide-react"
import { MEMORY_LAYERS, MEMORY_LAYER_CONFIG, normalizeMemoryLayer } from "@/lib/layers"
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

  return (
    <section className="overflow-hidden rounded-xl border border-border bg-card" aria-labelledby="recent-heading" aria-busy={isLoading}>
      <div className="flex flex-wrap items-center justify-between gap-3 px-6 pt-6 pb-4">
        <div>
          <h2 id="recent-heading" className="text-lg font-semibold">Recently remembered</h2>
          <p className="mt-1 text-sm text-muted-foreground">Open a memory to pick up the details.</p>
        </div>
        <Link href={projectHref("/memories", selectedRepoId)} className="text-sm font-medium text-primary hover:underline">View library</Link>
      </div>
      <div className="flex flex-wrap items-center gap-1 border-b border-border px-6 pb-4" role="group" aria-label="Filter recent memories by layer">
        <SlidersHorizontal className="mr-2 h-3.5 w-3.5 text-muted-foreground" aria-hidden="true" />
        {(["all", ...MEMORY_LAYERS] as const).map((value) => (
          <button key={value} type="button" aria-pressed={layer === value} onClick={() => setLayer(value)}
            className={cn("rounded-md px-2.5 py-1.5 text-xs font-medium transition-colors", layer === value ? "bg-accent text-accent-foreground" : "text-muted-foreground hover:bg-secondary")}>
            {value === "all" ? "All layers" : MEMORY_LAYER_CONFIG[value].label}
          </button>
        ))}
      </div>
      {isLoading ? (
        <div role="status" className="space-y-6 p-6">
          <span className="sr-only">Loading recent memories</span>{[0, 1, 2].map((n) => <div key={n} className="space-y-3">
            <div className="shimmer h-4 w-4/5 rounded" />
            <div className="shimmer h-3 w-2/5 rounded" />
          </div>)}</div>
      ) : unavailable ? (
        <div className="p-8 text-sm text-muted-foreground">Recent memories are unavailable. Retry the connection above to load your library.</div>
      ) : visible.length === 0 ? (
        <div className="px-6 py-12 text-center">
          <Library className="mx-auto mb-4 h-7 w-7 text-muted-foreground" />
          <h3 className="font-medium">{memories.length ? "No recent memories in this layer" : "Start with something worth remembering"}</h3>
          <p className="mx-auto mt-2 max-w-sm text-sm leading-6 text-muted-foreground">{memories.length ? "Choose another layer or open the library to explore older memories." : "Add a decision, a useful fact, or a lesson learned with New memory."}</p>
        </div>
      ) : (
        <div className="divide-y divide-border">
          {visible.map((memory) => {
            const config = MEMORY_LAYER_CONFIG[normalizeMemoryLayer(memory.layer)]
            const Icon = config.icon
            const date = new Date(memory.createdAt)
            const validDate = !Number.isNaN(date.getTime())
            return (
              <details key={memory.id} className="group open:bg-secondary/30">
                <summary className="flex list-none items-start gap-3 px-6 py-5 hover:bg-secondary/40 [&::-webkit-details-marker]:hidden">
                  <span className={cn("mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg", config.bgColor)}>
                    <Icon className={cn("h-4 w-4", config.color)} />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="mb-2 line-clamp-2 text-sm leading-6 group-open:hidden">{memory.content}</span>
                    <span className="hidden text-sm font-medium group-open:block group-open:mb-2">Memory details</span>
                    <span className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
                      <span className={cn("font-medium", config.color)}>{config.label}</span>
                      <span>{memory.category}</span>
                      {validDate && <time dateTime={memory.createdAt} title={date.toLocaleString()}>{date.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })}</time>}
                    </span>
                  </span>
                  <ChevronDown className="mt-1 h-4 w-4 shrink-0 text-muted-foreground transition-transform group-open:rotate-180" />
                </summary>
                <div className="px-6 pb-6 sm:pl-[4.5rem]">
                  <p className="whitespace-pre-wrap break-words text-sm leading-7">{memory.content}</p>
                  {!!memory.tags?.length && <div className="mt-4 flex flex-wrap gap-2">{memory.tags.map((tag) => <span key={tag} className="rounded-md bg-secondary px-2 py-1 text-xs text-muted-foreground">{tag}</span>)}</div>}
                </div>
              </details>
            )
          })}
        </div>
      )}
    </section>
  )
}
