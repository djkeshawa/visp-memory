import { shortDate } from "@/lib/memory-trust"
import type { Memory } from "@/lib/types"

/** Provenance a reviewer needs before relying on a memory, from what the server reported. */
export function RowDetails({ memory }: { memory: Memory }) {
  const external = memory.tags?.includes("provenance:external")
  const flags = memory.qualityFlags || []
  return (
    <details className="mt-2 text-xs text-muted-foreground">
      <summary className="inline-block rounded py-1 font-medium text-highlight">Source and quality</summary>
      <dl className="mt-2 grid max-w-xl grid-cols-[auto_1fr] gap-x-4 gap-y-1 break-words">
        <dt>Source</dt><dd>{memory.source || "Not recorded"}</dd>
        <dt>Evidence</dt><dd>{memory.evidenceIds?.length || 0} linked records</dd>
        <dt>Review</dt><dd>{memory.approvedAt ? `Reviewed ${shortDate(memory.approvedAt) ?? ""}` : "No approval recorded"}</dd>
        {memory.validTo ? <><dt>Valid until</dt><dd>{shortDate(memory.validTo)}</dd></> : null}
        {memory.files?.length ? <><dt>Files</dt><dd className="font-mono">{memory.files.join(", ")}</dd></> : null}
        {flags.length ? <><dt>Flags</dt><dd>{flags.map((flag) => flag.replaceAll("_", " ")).join(", ")}</dd></> : null}
      </dl>
      {external ? (
        <p className="mt-2 max-w-xl">
          Dashboard and API notes stay searchable, but their external provenance keeps them out of task briefs and automatic
          context. Verify the original source, then record your own reviewed conclusion with the local CLI, including a source
          reference. Marking a note active does not change its provenance.
        </p>
      ) : null}
    </details>
  )
}
