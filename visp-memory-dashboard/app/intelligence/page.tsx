"use client"

import { Suspense } from "react"
import { AlertTriangle, RefreshCw } from "lucide-react"
import { PageHeader, Pill, Stat } from "@/components/strata/primitives"
import { Button } from "@/components/ui/button"
import { SectionCard } from "./_components/section-card"
import { DuplicateItems, FreshnessItems, GraphEvidenceItems, ReportItems } from "./_components/sections"
import { formatAsOf, thresholdText } from "./_components/thresholds"
import { DECAY_HALFLIFE_DAYS, useIntelligence } from "./_components/use-intelligence"

function IntelligenceContent() {
  const { repoId, report, graphEvidence, duplicates, freshness, isLoading, errorMessage, reload } = useIntelligence()
  const reportSections = Object.entries(report?.sections ?? {})
  const asOf = formatAsOf(report?.asOf)
  const eyebrow = [
    report ? `report v${report.schemaVersion}` : null,
    repoId ?? report?.repoId ?? "all projects",
    asOf ? `as of ${asOf}` : null,
  ].filter(Boolean).join(" · ")

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow={<span className="font-mono text-xs">{eyebrow}</span>}
        title="Intelligence"
        actions={
          <Button variant="outline" className="h-11" onClick={() => void reload()} disabled={isLoading}>
            <RefreshCw aria-hidden="true" className={isLoading ? "animate-spin" : undefined} />Regenerate report
          </Button>
        }
      />

      {errorMessage ? (
        <div role="alert" className="flex items-start gap-2 rounded-xl border border-destructive/30 bg-destructive/12 p-4 text-sm">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" aria-hidden="true" />
          <span>{errorMessage}</span>
        </div>
      ) : null}

      <section aria-label="Report summary" aria-busy={isLoading} className="surface grid grid-cols-2 gap-5 rounded-2xl p-5 sm:p-6 lg:grid-cols-4">
        <Stat label="Memories" value={report?.summary.totalMemories ?? "-"} />
        <Stat label="Relationships" value={report?.summary.totalRelationships ?? "-"} />
        <Stat label="Active intents" value={report?.summary.activeIntents ?? "-"} />
        <Stat
          label="Report sections with findings"
          value={report ? <>{report.summary.nonEmptySections} <span className="text-sm font-normal text-muted-foreground">of {reportSections.length}</span></> : "-"}
        />
      </section>

      <div className="flex flex-wrap gap-x-6 gap-y-2 text-[13px] text-muted-foreground">
        <span className="inline-flex flex-wrap items-center gap-2"><Pill tone="success">Stored fact</Pill>read straight from the store</span>
        <span className="inline-flex flex-wrap items-center gap-2"><Pill tone="inferred">Inferred</Pill>a recommendation to check, not a fact</span>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        {reportSections.map(([key, section]) => (
          <SectionCard
            key={key}
            id={`section-${key}`}
            title={section.title}
            kind={section.kind}
            threshold={thresholdText(key, report?.thresholds ?? {})}
            isLoading={isLoading}
            count={section.items.length}
            emptyLabel={`No ${section.title.toLowerCase()} returned.`}
            unit="findings"
          >
            <ReportItems items={section.items} />
          </SectionCard>
        ))}
        <SectionCard
          id="section-graph-evidence" title="Graph evidence" kind="stored_fact" threshold="Links that record how they were established"
          isLoading={isLoading} count={graphEvidence.length} emptyLabel="No graph evidence returned." unit="links"
        >
          <GraphEvidenceItems links={graphEvidence} />
        </SectionCard>
        <SectionCard
          id="section-duplicates" title="Duplicates" kind="inferred_recommendation" threshold="Memories in the same layer with similar text"
          isLoading={isLoading} count={duplicates.length} emptyLabel="No duplicate candidates returned." unit="candidates"
        >
          <DuplicateItems candidates={duplicates} />
        </SectionCard>
        <SectionCard
          id="section-freshness" title="Freshness" kind="inferred_recommendation"
          threshold={`Projected importance decay, ${DECAY_HALFLIFE_DAYS}-day half-life; stable memories are hidden`}
          isLoading={isLoading} count={freshness.length} emptyLabel="No stale freshness candidates returned." unit="memories"
        >
          <FreshnessItems candidates={freshness} />
        </SectionCard>
      </div>
    </div>
  )
}

export default function IntelligencePage() {
  return (
    <Suspense fallback={null}>
      <IntelligenceContent />
    </Suspense>
  )
}
