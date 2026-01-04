export function StatsSkeleton() {
  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
      {Array.from({ length: 4 }).map((_, i) => (
        <div key={i} className="glass rounded-xl p-5">
          <div className="flex items-start gap-4">
            <div className="h-12 w-12 rounded-xl shimmer" />
            <div className="flex-1 space-y-2">
              <div className="h-4 w-20 rounded shimmer" />
              <div className="h-8 w-16 rounded shimmer" />
              <div className="h-3 w-24 rounded shimmer" />
            </div>
          </div>
        </div>
      ))}
    </div>
  )
}
