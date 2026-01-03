"use client"

import { useEffect, useState, useRef, type ReactNode } from "react"
import { motion, useInView } from "framer-motion"
import { cn } from "@/lib/utils"

interface AnimatedStatsCardProps {
  title: string
  value: number
  icon: ReactNode
  change?: string
  gradient: string
}

function useCountUp(end: number, duration = 1000, startCounting = false) {
  const [count, setCount] = useState(0)

  useEffect(() => {
    if (!startCounting) return

    let startTime: number | null = null
    let animationFrame: number

    const animate = (timestamp: number) => {
      if (!startTime) startTime = timestamp
      const progress = Math.min((timestamp - startTime) / duration, 1)

      setCount(Math.floor(progress * end))

      if (progress < 1) {
        animationFrame = requestAnimationFrame(animate)
      }
    }

    animationFrame = requestAnimationFrame(animate)
    return () => cancelAnimationFrame(animationFrame)
  }, [end, duration, startCounting])

  return count
}

export function AnimatedStatsCard({ title, value, icon, change, gradient }: AnimatedStatsCardProps) {
  const ref = useRef<HTMLDivElement>(null)
  const isInView = useInView(ref, { once: true })
  const count = useCountUp(value, 1000, isInView)

  return (
    <motion.div
      ref={ref}
      initial={{ opacity: 0, y: 20 }}
      animate={isInView ? { opacity: 1, y: 0 } : {}}
      transition={{ duration: 0.4 }}
      whileHover={{ y: -4 }}
      className="glass rounded-lg p-5 cursor-pointer group transition-shadow hover:shadow-md"
    >
      <div className="flex items-start gap-4">
        <div
          className={cn(
            "flex h-11 w-11 items-center justify-center rounded-md transition-transform",
            gradient,
          )}
        >
          {icon}
        </div>
        <div className="flex-1">
          <p className="text-sm font-medium text-muted-foreground">{title}</p>
          <p className="text-3xl font-semibold text-foreground mt-1">{count.toLocaleString()}</p>
          {change && <p className="text-xs text-muted-foreground mt-1">{change}</p>}
        </div>
      </div>
    </motion.div>
  )
}
