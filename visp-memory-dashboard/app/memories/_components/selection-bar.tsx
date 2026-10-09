import { GitMerge, Trash2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

interface SelectionBarProps {
  count: number
  busy: boolean
  onMerge: () => void
  onTrash: () => void
}

/** Actions for the selected memories. Trash is recoverable from the Trash tab. */
export function SelectionBar({ count, busy, onMerge, onTrash }: SelectionBarProps) {
  return (
    <div className={cn("flex flex-wrap items-center gap-x-3.5 gap-y-2 rounded-xl border px-3.5 py-2.5", count ? "border-ring/40 bg-accent" : "border-border bg-card")}>
      <span role="status" className="text-sm font-semibold">{count} selected</span>
      <Button size="sm" variant="outline" className="h-10" onClick={onMerge} disabled={count < 2 || busy}>
        <GitMerge aria-hidden="true" />Merge selected ({count})
      </Button>
      <Button size="sm" variant="outline" className="h-10" onClick={onTrash} disabled={count < 1 || busy}>
        <Trash2 aria-hidden="true" />{busy ? "Moving to trash…" : "Move to trash"}
      </Button>
      <span className="text-xs text-muted-foreground sm:ml-auto">
        Merging keeps one canonical memory and moves the rest into recoverable history. Trashed memories can be restored.
      </span>
    </div>
  )
}
