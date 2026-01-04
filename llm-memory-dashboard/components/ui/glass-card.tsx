import { cn } from "@/lib/utils"
import type { ReactNode } from "react"

interface GlassCardProps {
  children: ReactNode
  className?: string
  hover?: boolean
}

export function GlassCard({ children, className, hover = false }: GlassCardProps) {
  return (
    <div
      className={cn(
        "glass rounded-xl p-5 transition-all duration-300",
        hover && "hover:-translate-y-1 hover:shadow-lg cursor-pointer",
        className,
      )}
    >
      {children}
    </div>
  )
}
