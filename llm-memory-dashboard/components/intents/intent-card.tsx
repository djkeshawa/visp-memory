"use client"

import { motion } from "framer-motion"
import { Circle, CheckCircle2 } from "lucide-react"
import { staggerItem } from "@/lib/animations"
import type { Intent } from "@/lib/types"
import { cn } from "@/lib/utils"

interface IntentCardProps {
  intent: Intent
}

export function IntentCard({ intent }: IntentCardProps) {
  const isCompleted = intent.status === "completed"

  return (
    <motion.div
      variants={staggerItem}
      whileHover={{ x: 4 }}
      className={cn(
        "glass rounded-xl p-4 flex items-start gap-4 cursor-pointer transition-all",
        isCompleted && "opacity-75",
      )}
    >
      {isCompleted ? (
        <CheckCircle2 className="h-5 w-5 text-success shrink-0 mt-0.5" />
      ) : (
        <Circle className="h-5 w-5 text-intent shrink-0 mt-0.5" />
      )}
      <div className="flex-1 min-w-0">
        <p className={cn("text-sm font-medium text-foreground", isCompleted && "line-through text-muted-foreground")}>
          {intent.description}
        </p>
        <p className="text-xs text-muted-foreground mt-1 capitalize">{intent.priority} priority</p>
      </div>
    </motion.div>
  )
}
