"use client"

import { Check, RefreshCw, ShieldQuestion } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Pill } from "@/components/strata/primitives"
import type { TaskMemoryBrief } from "@/lib/types"
import { cn } from "@/lib/utils"
import { SECTION_ORDER } from "./sections"
import { SectionBlock } from "./section-block"
import { TokenMeter } from "./token-meter"

function Callout({ tone, children }: { tone: "success" | "warning"; children: React.ReactNode }) {
  return (
    <div className={cn("flex items-start gap-2 rounded-xl border p-3.5 text-sm", tone === "success" ? "border-success/30 bg-success/12 text-success" : "border-warning/30 bg-warning/12 text-warning")}>
      {tone === "success" ? <Check className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" /> : <ShieldQuestion className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />}
      <span>{children}</span>
    </div>
  )
}

/** The compiled brief: budget use, what the server flagged, then the cited evidence by section. */
export function BriefResult({ brief, isLoading, canCheck, copied, onCheck, onCopy }: {
  brief: TaskMemoryBrief
  isLoading: boolean
  canCheck: boolean
  copied: boolean
  onCheck: () => void
  onCopy: () => void
}) {
  const evidenceCount = SECTION_ORDER.reduce((sum, name) => sum + (brief.sections[name]?.length ?? 0), 0)
  return (
    <section aria-labelledby="brief-h" className="surface min-w-0 overflow-hidden rounded-2xl">
      <div className="flex flex-col gap-4 border-b border-border p-5 sm:p-6">
        <div className="flex flex-wrap items-center justify-between gap-2.5">
          <h2 id="brief-h" className="flex items-center gap-2.5 text-lg font-semibold">Compiled brief <Pill className="capitalize">{brief.taskProfile.action}</Pill></h2>
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" onClick={onCheck} disabled={isLoading || !canCheck}>
              <RefreshCw className={cn("h-4 w-4", isLoading && "animate-spin")} aria-hidden="true" />Check for changes
            </Button>
            <Button onClick={onCopy} disabled={!brief.context}>{copied ? "Copied" : "Copy context"}</Button>
          </div>
        </div>
        <TokenMeter brief={brief} />
        {brief.unchanged ? <Callout tone="success">The cited context has not changed since the previous brief.</Callout> : null}
        {brief.abstained ? <Callout tone="warning">{brief.abstentionReason || "The brief abstained because it could not find reliable evidence."}</Callout> : null}
        {brief.truncated ? <Callout tone="warning">Truncated to fit the token budget{brief.metrics.omittedCount ? `: ${brief.metrics.omittedCount} candidate${brief.metrics.omittedCount === 1 ? "" : "s"} left out` : ""}. Raise the budget to include more.</Callout> : null}
      </div>
      {!brief.unchanged ? (
        <>
          {SECTION_ORDER.map((name) => (
            <SectionBlock key={name} name={name} items={brief.sections[name] ?? []} contradictions={name === "warnings" ? brief.contradictions : []} />
          ))}
          {evidenceCount === 0 && !brief.contradictions.length ? <p className="border-b border-border px-5 py-4 text-sm text-muted-foreground sm:px-6">No evidence was selected for this brief.</p> : null}
          <section aria-labelledby="brief-unknowns" className="flex flex-col gap-2 px-5 py-4 sm:px-6">
            <h3 id="brief-unknowns" className="eyebrow">Unknowns</h3>
            {brief.unknowns.length ? (
              <ul className="flex flex-col gap-1.5 text-sm">{brief.unknowns.map((unknown) => <li key={unknown}>{unknown}</li>)}</ul>
            ) : <p className="text-sm text-muted-foreground">The server listed no unknowns to verify.</p>}
            <p className="text-xs text-muted-foreground">
              {brief.intent ? `Matched intent: ${brief.intent.description}` : "No active intent matched this task."} Ranking scores order candidates; they are not probabilities, and memory is not authoritative.
            </p>
            {brief.context ? (
              <details className="well mt-2 rounded-xl p-3.5 text-sm">
                <summary className="font-medium">Compiled context text</summary>
                <pre className="mt-3 max-h-[28rem] overflow-auto whitespace-pre-wrap break-words font-mono text-[13px] leading-6">{brief.context}</pre>
              </details>
            ) : null}
          </section>
        </>
      ) : null}
    </section>
  )
}
