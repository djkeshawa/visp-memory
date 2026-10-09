import type { TaskBriefSectionName } from "@/lib/types"

export const SECTION_ORDER: TaskBriefSectionName[] = ["warnings", "decisions", "knowledge", "history"]

/** Label and theme classes per section; the same token colours serve the headings and the meter. */
export const SECTION_STYLE: Record<TaskBriefSectionName, { label: string; text: string; bar: string }> = {
  warnings: { label: "Warnings", text: "text-destructive", bar: "bg-destructive" },
  decisions: { label: "Decisions", text: "text-intent", bar: "bg-intent" },
  knowledge: { label: "Knowledge", text: "text-semantic", bar: "bg-semantic" },
  history: { label: "History", text: "text-muted-foreground", bar: "bg-muted-foreground" },
}
