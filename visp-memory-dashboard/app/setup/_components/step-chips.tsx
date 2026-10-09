import { cn } from "@/lib/utils"

/**
 * Setup progress, from what this page can actually see: a project is selected, and whether
 * semantic search is ready. Accounts are not shown — this page is reachable with auth disabled.
 */
export function StepChips({ hasProject, searchReady }: { hasProject: boolean; searchReady: boolean }) {
  const steps = [
    { label: "Project", done: hasProject, current: false },
    { label: "Search", done: searchReady, current: true },
  ]
  return (
    <ol aria-label="Setup steps" className="m-0 flex list-none flex-wrap gap-1.5 p-0 text-[13px]">
      {steps.map((step, index) => (
        <li
          key={step.label}
          aria-current={step.current ? "step" : undefined}
          className={cn(
            "inline-flex h-8 items-center gap-2 rounded-full px-3",
            step.current ? "bg-primary font-semibold text-primary-foreground" : step.done ? "bg-success/12 text-success" : "bg-secondary text-muted-foreground",
          )}
        >
          {`${index + 1} · ${step.label}`}{step.done ? <span aria-label="done"> ✓</span> : null}
        </li>
      ))}
    </ol>
  )
}
