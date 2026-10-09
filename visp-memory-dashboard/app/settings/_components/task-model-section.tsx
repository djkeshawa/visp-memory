import { Pill } from "@/components/strata/primitives"
import { Button } from "@/components/ui/button"
import type { ModelRoutingStatus } from "@/lib/types"

export function TaskModelSection({ routing, message, isTesting, onTest }: {
  routing: ModelRoutingStatus
  message: string | null
  isTesting: boolean
  onTest: () => void
}) {
  return (
    <section aria-labelledby="task-model-heading" className="surface rounded-2xl p-5 sm:p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <h2 id="task-model-heading" className="text-lg font-semibold">Task model</h2>
          <Pill tone={routing.configured ? "success" : "neutral"}>{routing.configured ? "Configured" : "Not configured"}</Pill>
        </div>
        <Button variant="outline" className="h-10" onClick={onTest} disabled={isTesting || !routing.configured}>
          {isTesting ? "Testing…" : "Test model"}
        </Button>
      </div>
      <p className="mt-2 text-sm leading-6 text-muted-foreground">
        {routing.configured
          ? `${routing.provider} · ${routing.model || "provider default"}`
          : "No server model is configured. MCP client sampling remains available when the client supports it."}
      </p>
      {routing.tasks.length > 0 ? (
        <ul aria-label="Tasks routed to this model" className="mt-3 flex flex-wrap gap-1.5">
          {routing.tasks.map((task) => <li key={task}><code className="rounded-md bg-secondary px-2 py-1 font-mono text-xs text-muted-foreground">{task}</code></li>)}
        </ul>
      ) : null}
      {message ? <p role="status" className="mt-3 text-sm text-muted-foreground">{message}</p> : null}
    </section>
  )
}
