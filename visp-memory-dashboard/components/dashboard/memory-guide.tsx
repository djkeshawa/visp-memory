import Link from "next/link"
import { ArrowUpRight, Network } from "lucide-react"
import { MEMORY_LAYERS, MEMORY_LAYER_CONFIG } from "@/lib/layers"
import { projectHref } from "@/lib/project-selection"

const descriptions = {
  raw: "Original captures, kept for reference.",
  episodic: "Events, decisions, and lessons learned.",
  semantic: "Facts and knowledge that carry forward.",
  intent: "Recorded goals and planned work.",
}

export function MemoryGuide({ repoId }: { repoId: string | null }) {
  return (
    <section className="rounded-xl border border-border bg-card p-6" aria-labelledby="layers-heading">
      <div className="mb-5 flex items-center justify-between">
        <h2 id="layers-heading" className="font-semibold">How memory is organized</h2>
        <Network className="h-4 w-4 text-muted-foreground" />
      </div>
      <div className="space-y-5">
        {MEMORY_LAYERS.map((layer) => {
          const config = MEMORY_LAYER_CONFIG[layer]
          return <div key={layer} className="flex gap-3">
            <span className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg ${config.bgColor}`}>
              <config.icon className={`h-4 w-4 ${config.color}`} />
            </span>
            <div>
              <p className="text-sm font-medium">{config.label}</p>
              <p className="mt-0.5 text-xs leading-5 text-muted-foreground">{descriptions[layer]}</p>
            </div>
          </div>
        })}
      </div>
      <Link href={projectHref("/graph", repoId)} className="mt-6 flex items-center justify-between border-t border-border pt-4 text-sm font-medium text-primary hover:underline">Explore connections<ArrowUpRight className="h-4 w-4" />
      </Link>
    </section>
  )
}
