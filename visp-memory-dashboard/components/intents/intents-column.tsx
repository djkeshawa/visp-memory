"use client"

import { motion } from "framer-motion"
import { Circle, CheckCircle2 } from "lucide-react"
import { IntentCard } from "./intent-card"
import { staggerContainer } from "@/lib/animations"
import type { Intent } from "@/lib/types"
import { cn } from "@/lib/utils"

interface IntentsColumnProps {
  title: string
  intents: Intent[]
  type: "active" | "completed"
  onComplete?: (intent: Intent) => void
  onClose?: (intent: Intent) => void
  onUpdate?: (intent: Intent, updates: { description: string; priority: number }) => void
  onReopen?: (intent: Intent) => void
}

export function IntentsColumn({ title, intents, type, onComplete, onClose, onUpdate, onReopen }: IntentsColumnProps) {
  const isEmpty = intents.length === 0

  return (
    <div className="space-y-4">
      {/* Column Header */}
      <div className="flex items-center gap-3">
        <h2 className="text-lg font-semibold text-foreground">{title}</h2>
        <span
          className={cn(
            "inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium",
            type === "active"
              ? "bg-amber-50 dark:bg-amber-900/30 text-amber-600 dark:text-amber-400"
              : "bg-emerald-50 dark:bg-emerald-900/30 text-emerald-600 dark:text-emerald-400",
          )}
        >
          {intents.length}
        </span>
      </div>

      {/* Intent Cards */}
      {isEmpty ? (
        <div className="glass rounded-lg p-8 border-2 border-dashed border-border text-center">
          {type === "active" ? (
            <>
              <Circle className="h-8 w-8 text-muted-foreground mx-auto mb-3" />
              <p className="text-sm text-muted-foreground">No active intents</p>
            </>
          ) : (
            <>
              <CheckCircle2 className="h-8 w-8 text-muted-foreground mx-auto mb-3" />
              <p className="text-sm text-muted-foreground">No recorded outcomes</p>
            </>
          )}
        </div>
      ) : (
        <motion.div className="space-y-3" variants={staggerContainer} initial="hidden" animate="visible">
          {intents.map((intent) => (
            <IntentCard
              key={intent.id}
              intent={intent}
              onComplete={onComplete}
              onClose={onClose}
              onUpdate={onUpdate}
              onReopen={onReopen}
            />
          ))}
        </motion.div>
      )}
    </div>
  )
}
