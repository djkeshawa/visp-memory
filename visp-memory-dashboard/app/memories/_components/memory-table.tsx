import { OctagonX, RotateCcw, Trash2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { LayerGlyph } from "@/components/strata/layer-glyph"
import { Pill } from "@/components/strata/primitives"
import { MEMORY_LAYER_CONFIG, normalizeMemoryLayer } from "@/lib/layers"
import { evidenceLabel, memoryTrustFlags, shortDate } from "@/lib/memory-trust"
import type { Memory } from "@/lib/types"
import { cn } from "@/lib/utils"
import { RowDetails } from "./row-details"
import type { LibraryStatus } from "./use-library"

interface MemoryTableProps {
  memories: Memory[]
  status: LibraryStatus
  selected: string[]
  onToggle: (id: string) => void
  onTrash: (memory: Memory) => void
  onRestore: (memory: Memory) => void
  onPurge: (memory: Memory) => void
}

const HEADER = "px-3 py-3 font-medium"
const ACTION = "h-10 w-10 text-muted-foreground"

export function MemoryTable({ memories, status, selected, onToggle, onTrash, onRestore, onPurge }: MemoryTableProps) {
  return (
    <div className="surface overflow-x-auto rounded-2xl">
      <table className="w-full min-w-[54rem] border-collapse text-left text-sm">
        <thead className="text-xs text-muted-foreground">
          <tr>
            <th scope="col" className="w-11 py-3 pl-[18px]"><span className="sr-only">Select</span></th>
            <th scope="col" className={HEADER}>Memory</th>
            <th scope="col" className={HEADER}>Layer</th>
            <th scope="col" className={HEADER}>Trust</th>
            <th scope="col" className={HEADER}>Created</th>
            <th scope="col" className={cn(HEADER, "pr-[18px] text-right")}>Actions</th>
          </tr>
        </thead>
        <tbody>
          {memories.map((memory) => {
            const layer = MEMORY_LAYER_CONFIG[normalizeMemoryLayer(memory.layer)]
            const flags = memoryTrustFlags(memory)
            const evidence = evidenceLabel(memory)
            const isSelected = selected.includes(memory.id)
            const date = shortDate(memory.createdAt)
            return (
              <tr key={memory.id} className={cn("border-t border-border align-top", isSelected && "bg-accent")}>
                <td className="py-3.5 pl-[18px]">
                  <input
                    type="checkbox"
                    checked={isSelected}
                    onChange={() => onToggle(memory.id)}
                    disabled={status !== "active"}
                    aria-label={`Select memory ${memory.id}`}
                    className="mt-0.5 h-[18px] w-[18px]"
                  />
                </td>
                <td className="max-w-[28.75rem] px-3 py-3.5">
                  <p className="line-clamp-2 leading-6">{memory.content}</p>
                  <p className="mt-1 break-all font-mono text-xs text-muted-foreground">
                    {memory.id}{memory.category ? ` · ${memory.category}` : ""}
                  </p>
                  {memory.tags?.length ? (
                    <div className="mt-1.5 flex flex-wrap gap-1">
                      {memory.tags.slice(0, 4).map((tag) => <span key={tag} className="rounded bg-secondary px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground">{tag}</span>)}
                    </div>
                  ) : null}
                  <RowDetails memory={memory} />
                </td>
                <td className="whitespace-nowrap px-3 py-3.5">
                  <span className={cn("inline-flex items-center gap-1.5 text-[13px] font-semibold", layer.color)}>
                    <LayerGlyph layer={memory.layer} />{layer.label}
                  </span>
                </td>
                <td className="px-3 py-3.5 text-[13px]">
                  <span className="block text-muted-foreground">
                    <span className={memory.approvedAt ? "text-success" : undefined}>{memory.approvedAt ? "Reviewed" : "Unreviewed"}</span>
                    {evidence ? ` · ${evidence}` : ""}
                  </span>
                  {flags.length ? (
                    <span className="mt-1.5 flex flex-wrap gap-1">
                      {flags.map((flag) => <Pill key={flag.label} tone={flag.tone}>{flag.label}</Pill>)}
                    </span>
                  ) : null}
                </td>
                <td className="whitespace-nowrap px-3 py-3.5 text-[13px] text-muted-foreground">
                  {date ? <time dateTime={memory.createdAt} title={new Date(memory.createdAt).toLocaleString()}>{date}</time> : null}
                </td>
                <td className="whitespace-nowrap py-2 pl-3 pr-[18px] text-right">
                  {status === "deleted" ? (
                    <Button variant="ghost" size="icon-lg" className={ACTION} title="Restore" aria-label={`Restore ${memory.id}`} onClick={() => onRestore(memory)}>
                      <RotateCcw aria-hidden="true" />
                    </Button>
                  ) : status === "active" || status === "archived" || status === "pending" ? (
                    <Button variant="ghost" size="icon-lg" className={ACTION} title="Move to trash" aria-label={`Move ${memory.id} to trash`} onClick={() => onTrash(memory)}>
                      <Trash2 aria-hidden="true" />
                    </Button>
                  ) : null}
                  {status !== "active" ? (
                    <Button variant="ghost" size="icon-lg" className="h-10 w-10 text-destructive" title="Permanently purge" aria-label={`Permanently purge ${memory.id}`} onClick={() => onPurge(memory)}>
                      <OctagonX aria-hidden="true" />
                    </Button>
                  ) : null}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
