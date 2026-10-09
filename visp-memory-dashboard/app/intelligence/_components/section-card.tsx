import type { ReactNode } from "react"
import { Pill } from "@/components/strata/primitives"
import type { MemoryIntelligenceReportKind } from "@/lib/types"

export const ITEM_LIMIT = 6

/** Stored facts are read from the store; inferred sections are recommendations to check. */
export function KindPill({ kind }: { kind: MemoryIntelligenceReportKind }) {
  return kind === "stored_fact" ? <Pill tone="success">Stored fact</Pill> : <Pill tone="inferred">Inferred</Pill>
}

interface SectionCardProps {
  id: string
  title: string
  kind: MemoryIntelligenceReportKind
  threshold: string
  isLoading: boolean
  count: number
  emptyLabel: string
  unit: string
  children: ReactNode
}

export function SectionCard({ id, title, kind, threshold, isLoading, count, emptyLabel, unit, children }: SectionCardProps) {
  return (
    <section aria-labelledby={id} className="surface flex min-w-0 flex-col gap-3 rounded-2xl p-5">
      <div className="flex items-center justify-between gap-3">
        <h2 id={id} className="text-base font-semibold">{title}</h2>
        <KindPill kind={kind} />
      </div>
      {threshold ? <p className="-mt-1 text-xs text-muted-foreground">{threshold}</p> : null}
      {isLoading ? (
        <div className="space-y-3" aria-hidden="true">
          <div className="shimmer h-4 w-28 rounded" />
          <div className="shimmer h-4 w-full rounded" />
          <div className="shimmer h-4 w-2/3 rounded" />
        </div>
      ) : count === 0 ? (
        <p className="text-sm text-muted-foreground">{emptyLabel}</p>
      ) : (
        <>
          <ul className="m-0 flex list-none flex-col p-0">{children}</ul>
          {count > ITEM_LIMIT ? <p className="text-xs text-muted-foreground">Showing {ITEM_LIMIT} of {count} {unit}.</p> : null}
        </>
      )}
    </section>
  )
}

export function ItemRow({ title, reason, meta, children }: { title: ReactNode; reason?: ReactNode; meta?: ReactNode; children?: ReactNode }) {
  return (
    <li className="flex flex-col gap-1 border-t border-border py-3 first:border-t-0 first:pt-0">
      {meta ? <span className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">{meta}</span> : null}
      <span className="line-clamp-2 text-sm font-medium">{title}</span>
      {reason ? <span className="text-[13px] leading-5 text-muted-foreground">{reason}</span> : null}
      {children}
    </li>
  )
}
