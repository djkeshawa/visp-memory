"use client"

import { motion } from "framer-motion"
import { staggerContainer, staggerItem } from "@/lib/animations"
import { MEMORY_LAYER_CONFIG, isRecallableLayer, normalizeMemoryLayer } from "@/lib/layers"
import { MemoryDetails } from "@/components/recall/memory-details"
import type { SearchResult } from "@/lib/types"
import { cn } from "@/lib/utils"

interface SearchResultsProps {
  results: SearchResult[]
  query: string
}

export function SearchResults({ results, query }: SearchResultsProps) {
  // Recall is intentionally never a raw-layer browser. The API excludes raw rows too,
  // but keeping the guard at this display boundary prevents a misconfigured/older server
  // response from turning a recall result into an accidental raw-data disclosure.
  const recallResults = results.filter((memory) => isRecallableLayer(memory.layer))

  if (recallResults.length === 0) {
    return (
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        className="glass rounded-lg p-8 text-center"
      >
        <p className="text-muted-foreground">
          No results found for &quot;<span className="text-foreground font-medium">{query}</span>&quot;
        </p>
      </motion.div>
    )
  }

  return (
    <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="space-y-4">
      <p className="text-sm text-muted-foreground">
        Found {recallResults.length} result{recallResults.length !== 1 && "s"} for &quot;
        <span className="text-foreground font-medium">{query}</span>&quot;
      </p>

      <div className="glass rounded-lg overflow-hidden">
        <motion.div className="divide-y divide-border" variants={staggerContainer} initial="hidden" animate="visible">
          {recallResults.map((memory) => {
            const config = MEMORY_LAYER_CONFIG[normalizeMemoryLayer(memory.layer)]
            const Icon = config.icon

            return (
              <motion.div
                key={memory.id}
                variants={staggerItem}
                whileHover={{ x: 4, backgroundColor: "var(--secondary)" }}
                className="flex items-start gap-4 p-4 transition-colors"
              >
                <div className={cn("flex h-8 w-8 items-center justify-center rounded-lg", config.bgColor)}>
                  <Icon className={cn("h-4 w-4", config.color)} />
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-sm text-foreground">{memory.content}</p>
                  <MemoryDetails memory={memory} />
                  <div className="flex items-center gap-2 mt-2">
                    <span
                      className={cn(
                        "inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium",
                        config.bgColor,
                        config.color,
                      )}
                    >
                      {config.label}
                    </span>
                    <span className="text-xs text-muted-foreground">{memory.category}</span>
                  </div>
                </div>
              </motion.div>
            )
          })}
        </motion.div>
      </div>
    </motion.div>
  )
}
