import { Check } from "lucide-react"
import type { Intent } from "@/lib/types"
import { cn } from "@/lib/utils"

type ReportedCheck = { description: string; status: string }

/**
 * Acceptance criteria recorded on the intent, matched by description against the checks a
 * workflow reported. A criterion counts as met only when the report says it passed.
 */
export function readCriteria(intent: Intent): { text: string; met: boolean }[] {
  const raw = intent.context?.acceptance_criteria
  if (!Array.isArray(raw)) return []
  const checks: ReportedCheck[] = Array.isArray(intent.context?.external_workflow?.checks) ? intent.context!.external_workflow.checks : []
  return raw.filter((item): item is string => typeof item === "string" && item.trim() !== "").map((text) => ({
    text,
    met: checks.some((check) => check.description === text && check.status === "passed"),
  }))
}

export function Criteria({ intent }: { intent: Intent }) {
  const criteria = readCriteria(intent)
  if (!criteria.length) return null
  const met = criteria.filter((item) => item.met).length
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex justify-between gap-3 text-xs text-muted-foreground">
        <span>Acceptance criteria</span>
        <span>{met} of {criteria.length} reported met</span>
      </div>
      <div className="h-1 overflow-hidden rounded-full bg-border" role="presentation">
        <div className="h-full rounded-full bg-success" style={{ width: `${(met / criteria.length) * 100}%` }} />
      </div>
      <ul className="mt-1 flex flex-col gap-1">
        {criteria.map((item) => (
          <li key={item.text} className={cn("flex items-start gap-2 text-sm", item.met ? "text-foreground" : "text-muted-foreground")}>
            <span aria-hidden="true" className={cn("mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded border", item.met ? "border-success bg-success/12 text-success" : "border-input")}>
              {item.met ? <Check className="h-3 w-3" /> : null}
            </span>
            <span><span className="sr-only">{item.met ? "Reported met: " : "Not reported met: "}</span>{item.text}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}
