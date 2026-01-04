export function GraphLegend() {
  const layers = [
    { color: "bg-blue-500", label: "Episodic" },
    { color: "bg-purple-500", label: "Semantic" },
    { color: "bg-amber-500", label: "Intent" },
  ]

  return (
    <div className="flex items-center gap-6">
      {layers.map((layer) => (
        <div key={layer.label} className="flex items-center gap-2">
          <div className={`h-3 w-3 rounded-full ${layer.color}`} />
          <span className="text-sm text-muted-foreground">{layer.label}</span>
        </div>
      ))}
    </div>
  )
}
