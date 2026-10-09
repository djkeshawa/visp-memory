import type { HTMLAttributes, ReactNode } from "react"
import type { TrustTone } from "@/lib/memory-trust"
import { cn } from "@/lib/utils"

const PILL_TONES: Record<TrustTone | "inferred", string> = {
  neutral: "bg-secondary text-secondary-foreground",
  info: "bg-accent text-accent-foreground",
  success: "bg-success/12 text-success",
  warning: "bg-warning/12 text-warning",
  danger: "bg-destructive/12 text-destructive",
  inferred: "border border-dashed border-highlight text-highlight",
}

/** A small rounded label. `inferred` marks a recommendation, never a stored fact. */
export function Pill({ tone = "neutral", className, children }: { tone?: TrustTone | "inferred"; className?: string; children: ReactNode }) {
  return <span className={cn("inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-0.5 text-xs font-medium", PILL_TONES[tone], className)}>{children}</span>
}

const DOT_TONES: Record<TrustTone, string> = {
  neutral: "bg-muted-foreground border-muted-foreground",
  info: "bg-highlight border-highlight",
  success: "bg-success border-success",
  warning: "bg-warning border-warning",
  danger: "bg-destructive border-destructive",
}

/** A status dot. `hollow` reads as "partial" (e.g. keyword fallback), solid as settled. */
export function StatusDot({ tone = "neutral", hollow = false, className }: { tone?: TrustTone; hollow?: boolean; className?: string }) {
  return <span aria-hidden="true" className={cn("inline-block h-2 w-2 shrink-0 rounded-full border-[1.5px]", DOT_TONES[tone], hollow && "bg-transparent", className)} />
}

export function PageHeader({ eyebrow, title, description, actions }: { eyebrow?: ReactNode; title: ReactNode; description?: ReactNode; actions?: ReactNode }) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0 flex-1 basis-64">
        {eyebrow ? <p className="mb-1.5 text-sm text-muted-foreground">{eyebrow}</p> : null}
        <h1>{title}</h1>
        {description ? <p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">{description}</p> : null}
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </header>
  )
}

/** A card section with an optional heading row. */
export function Panel({ title, titleId, aside, className, bodyClassName, children, ...rest }: {
  title?: ReactNode
  titleId?: string
  aside?: ReactNode
  className?: string
  bodyClassName?: string
  children: ReactNode
} & Omit<HTMLAttributes<HTMLElement>, "title">) {
  return (
    <section aria-labelledby={title && titleId ? titleId : undefined} className={cn("surface rounded-2xl", className)} {...rest}>
      {title ? (
        <div className="flex flex-wrap items-center justify-between gap-3 px-5 pb-3 pt-5 sm:px-6">
          <h2 id={titleId} className="text-[15px] font-semibold">{title}</h2>
          {aside}
        </div>
      ) : null}
      <div className={cn(title ? "px-5 pb-5 sm:px-6" : "p-5 sm:p-6", bodyClassName)}>{children}</div>
    </section>
  )
}

export function Stat({ label, value, hint, valueClassName }: { label: ReactNode; value: ReactNode; hint?: ReactNode; valueClassName?: string }) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <span className="text-[13px] text-muted-foreground">{label}</span>
      <span className={cn("text-[28px] font-semibold leading-tight tracking-tight tabular-nums", valueClassName)}>{value}</span>
      {hint ? <span className="text-xs text-muted-foreground">{hint}</span> : null}
    </div>
  )
}
