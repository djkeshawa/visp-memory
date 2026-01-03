import { cn } from "@/lib/utils"

interface SkeletonCardProps {
  className?: string
}

export function SkeletonCard({ className }: SkeletonCardProps) {
  return (
    <div className={cn("glass rounded-xl p-5", className)}>
      <div className="flex items-start gap-4">
        <div className="h-10 w-10 rounded-lg shimmer" />
        <div className="flex-1 space-y-2">
          <div className="h-4 w-24 rounded shimmer" />
          <div className="h-8 w-16 rounded shimmer" />
        </div>
      </div>
    </div>
  )
}
