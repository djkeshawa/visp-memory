import type { Intent } from "@/lib/types"

export function WorkflowDetails({ intent }: { intent: Intent }) {
  const report = intent.context?.external_workflow
  const suggestion = intent.context?.completion_evaluation
  const history = intent.context?.workflow_history
  if (!report && !suggestion) return <p className="mt-2 text-xs text-muted-foreground">Waiting for a completion report from your assistant or workflow.</p>
  return (
    <details className="mt-3 rounded-md border border-border p-3 text-xs">
      <summary className="cursor-pointer font-medium">{report ? `Reported ${report.status} by ${report.source}` : "Completion suggested · awaiting workflow confirmation"}</summary>
      <div className="mt-3 space-y-3 text-muted-foreground">
        <p>{report?.summary || suggestion?.reason}</p>
        {report && <p>Task: {report.task_id} · Revision {report.revision}</p>}
        {Array.isArray(report?.checks) && report.checks.length > 0 && <ul className="space-y-1">
          {report.checks.map((check: {description: string; status: string}, index: number) => <li key={index}>{check.status === "passed" ? "✓" : "○"} {check.description} — {check.status}</li>)}
        </ul>}
        {Array.isArray(report?.evidence) && report.evidence.map((item: {description: string; url?: string}, index: number) => (
          <p key={index}>{item.url && /^https?:\/\//i.test(item.url)
            ? <a className="text-primary underline" href={item.url} target="_blank" rel="noopener noreferrer">{item.description}</a>
            : item.description}</p>
        ))}
        {Array.isArray(history) && history.length > 0 && <div className="space-y-1 border-t border-border pt-2">
          <p className="font-medium text-foreground">Status history</p>
          {history.slice(-5).reverse().map((item: {event_id: string; status: string; recorded_at: string}) => <p key={item.event_id}>{item.status} · {new Date(item.recorded_at).toLocaleString()}</p>)}
        </div>}
        <p>This reflects the source's report. It does not approve releases or grant permissions.</p>
      </div>
    </details>
  )
}
