import Link from "next/link"
import { Pill } from "@/components/strata/primitives"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import type { EmbeddingIndexStatus, EmbeddingReindexResult, ProviderDiagnostic, StorageDiagnostics } from "@/lib/types"
import { formatLastChecked, formatStatus } from "./format"

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-0.5 break-words text-sm font-medium">{value}</dd>
    </div>
  )
}

export function SearchSection({ index, storage, providers, reindexResult, isLoading, isReindexing, onCheck, onReindex }: {
  index: EmbeddingIndexStatus
  storage: StorageDiagnostics | null
  providers: ProviderDiagnostic[]
  reindexResult: EmbeddingReindexResult | null
  isLoading: boolean
  isReindexing: boolean
  onCheck: () => void
  onReindex: (dryRun: boolean) => void
}) {
  const semantic = index.status === "available"
  const reindexBlocked = storage?.capabilities.reindex === false
  const checked = providers.find((item) => item.provider === (index.effectiveProvider ?? index.provider))?.lastChecked
  const indexed = typeof index.indexedMemories === "number" ? `${index.indexedMemories} of ${index.matchedMemories}` : "Unknown"
  return (
    <section aria-labelledby="search-heading" className={cn("surface rounded-2xl p-5 sm:p-6", index.needsReindex && "border-warning/50")}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1 basis-72">
          <h2 id="search-heading" className="text-lg font-semibold">Search</h2>
          <p className="mt-1 text-sm leading-6 text-muted-foreground">
            Recall is using <strong className="font-semibold text-foreground">{semantic ? "meaning-based search" : "keyword search"}</strong>.{" "}
            {semantic ? index.message : "Connect an embedding provider on the server for meaning-based matches."}
          </p>
        </div>
        <Link href="/setup" className="inline-flex min-h-10 items-center text-sm font-medium text-highlight underline underline-offset-4">Open search setup guide</Link>
      </div>
      <dl className="well mt-5 grid gap-4 rounded-xl p-4 sm:grid-cols-3 lg:grid-cols-5">
        <Detail label="Status" value={formatStatus(index.status)} />
        <Detail label="Model" value={index.model || "Not configured"} />
        <Detail label="Dimension" value={typeof index.dimension === "number" ? String(index.dimension) : "—"} />
        <Detail label="Indexed" value={indexed} />
        <Detail label="Last checked" value={formatLastChecked(checked)} />
      </dl>
      {index.legacyCollections.length > 0 ? (
        <p className="mt-4 rounded-xl bg-warning/12 p-3 text-sm text-warning">Legacy vector collections detected: {index.legacyCollections.join(", ")}</p>
      ) : null}
      {reindexResult ? (
        <p role="status" className="well mt-4 rounded-xl p-3 text-sm text-muted-foreground">
          {reindexResult.message} Matched {reindexResult.matchedMemories}; rebuilt {reindexResult.reindexedMemories}; failed {reindexResult.failedMemories}.
        </p>
      ) : null}
      <div className="mt-5 flex flex-wrap items-center gap-2">
        <Button variant="outline" className="h-10" onClick={onCheck} disabled={isLoading}>Check again</Button>
        <Button variant="outline" className="h-10" onClick={() => onReindex(true)} disabled={isReindexing || reindexBlocked}>Dry run</Button>
        <Button className="h-10" onClick={() => onReindex(false)} disabled={isReindexing || !semantic || reindexBlocked}>Rebuild index</Button>
        {!semantic ? <Pill tone="neutral">Rebuild needs a connected provider</Pill> : null}
      </div>
    </section>
  )
}
