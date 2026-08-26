"use client"

import { motion } from "framer-motion"
import { staggerContainer, staggerItem } from "@/lib/animations"
import { MEMORY_LAYER_CONFIG, normalizeMemoryLayer } from "@/lib/layers"
import type { Memory } from "@/lib/types"
import { cn } from "@/lib/utils"

interface RecentActivityProps {
  memories: Memory[]
}

export function RecentActivity({ memories }: RecentActivityProps) {
  return (
    <div className="glass rounded-lg overflow-hidden">
      <div className="p-5 border-b border-border">
        <h2 className="text-lg font-semibold text-foreground">Recent Activity</h2>
        <p className="text-sm text-muted-foreground mt-1">Latest memories from your system</p>
      </div>

      <motion.div
        key={memories.length}
        className="divide-y divide-border"
        variants={staggerContainer}
        initial="hidden"
        animate="visible"
      >
        {memories.length === 0 ? (
          <div className="p-6 text-sm text-muted-foreground">No recent memories yet.</div>
        ) : null}

        {memories.map((memory) => {
          const config = MEMORY_LAYER_CONFIG[normalizeMemoryLayer(memory.layer)]
          const Icon = config.icon

          return (
            <motion.div
              key={memory.id}
              variants={staggerItem}
              whileHover={{ x: 4, backgroundColor: "var(--secondary)" }}
              className="flex items-start gap-4 p-4 cursor-pointer transition-colors"
            >
              <div className={cn("flex h-8 w-8 items-center justify-center rounded-lg", config.bgColor)}>
                <Icon className={cn("h-4 w-4", config.color)} />
              </div>
              <div className="flex-1 min-w-0">
                <p className="text-sm text-foreground line-clamp-2">{memory.content}</p>
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
  )
}
