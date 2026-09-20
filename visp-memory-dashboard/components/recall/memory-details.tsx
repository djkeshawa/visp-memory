import type { SearchResult } from "@/lib/types"

export function MemoryDetails({ memory }: { memory: SearchResult }) {
  const flags = memory.qualityFlags || []
  const expired = !!memory.validTo && Date.parse(memory.validTo) < Date.now()
  const conflicts = flags.some((flag) => /contradict|conflict/.test(flag)) || !!memory.metadata?.conflict
  const stale = expired || flags.some((flag) => /stale|decay/.test(flag))
  const external = memory.tags?.includes("provenance:external")
  const method = memory.retrievalMethod === "semantic" ? "Semantic search"
    : memory.retrievalMethod === "keyword" ? "Keyword search" : null
  return (
    <div className="mt-3 space-y-2 text-xs">
      <div className="flex flex-wrap gap-2">
        {method && <span className="rounded bg-secondary px-2 py-1 text-muted-foreground">{method}</span>}
        {external && <span className="rounded bg-intent/15 px-2 py-1">Excluded from task briefs</span>}
        {stale && <span className="rounded bg-intent/15 px-2 py-1">May be stale · review before using</span>}
        {conflicts && <span className="rounded bg-destructive/10 px-2 py-1 text-destructive">Possible conflict · review sources</span>}
        {memory.epistemicStatus === "unknown" && <span className="rounded bg-secondary px-2 py-1">Unverified knowledge</span>}
      </div>
      {external && <p className="text-muted-foreground">
        Dashboard and API notes remain searchable, but their external provenance excludes them from task briefs and automatic context.
        Verify the original source, then use the local CLI to record your own reviewed conclusion in the same project and store, including a source reference.
        Marking a note active does not change its provenance.
      </p>}
      <details className="rounded-lg border border-border p-3">
        <summary className="cursor-pointer font-medium">Match explanation and quality</summary>
        <div className="mt-3 space-y-2 break-words text-muted-foreground">
          {memory.matchExplanation && <p>{memory.matchExplanation}</p>}
          {!method && memory.similarity !== undefined && <p>The server did not report the search method.</p>}
          {memory.relevanceScore !== undefined && <p>Ranking score: {memory.relevanceScore.toFixed(2)}. This orders results; it is not a probability of correctness.</p>}
          <p>Source: {memory.source || "Not recorded"}</p>
          <p>{memory.evidenceIds?.length || 0} linked evidence records</p>
          <p>{memory.approvedAt ? `Reviewed ${new Date(memory.approvedAt).toLocaleDateString()}` : "No approval recorded"}</p>
          {memory.validTo && <p>Valid until: {new Date(memory.validTo).toLocaleDateString()}</p>}
          {!!memory.files?.length && <p>Files: {memory.files.join(", ")}</p>}
          {!!flags.length && <p>Quality flags: {flags.map((flag) => flag.replaceAll("_", " ")).join(", ")}</p>}
        </div>
      </details>
    </div>
  )
}
