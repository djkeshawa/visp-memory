import { Pill } from "@/components/strata/primitives"
import { MEMORY_LAYER_CONFIG } from "@/lib/layers"
import type { GraphData, GraphLink, GraphNode, RelationshipConfidence } from "@/lib/types"
import { cn } from "@/lib/utils"
import { LINK_KIND_LABEL, linkKind } from "./graph-types"

const KIND_TONE = { observed: "success", inferred: "neutral", ambiguous: "warning", manual: "info" } as const

function evidenceSource(link: GraphLink): string | null {
  const evidence = link.evidence
  if (!evidence) return null
  if (evidence.source) return evidence.source
  const file = [evidence.source_file, evidence.source_location].filter(Boolean).join(":")
  return file || null
}

function Relationship({ link, other, onSelect }: { link: GraphLink; other: GraphNode | undefined; onSelect: (id: string) => void }) {
  const kind: RelationshipConfidence = linkKind(link.evidence)
  const otherId = other?.id
  const score = link.evidence?.confidence_score
  const source = evidenceSource(link)
  return (
    <li className="flex flex-col gap-1 border-t border-border py-3">
      <span className="flex items-start justify-between gap-2">
        {otherId ? (
          <button type="button" onClick={() => onSelect(otherId)} className="min-h-10 min-w-0 text-left font-medium text-highlight hover:underline">
            {other?.label || otherId}
          </button>
        ) : <span className="font-medium">Unknown node</span>}
        {link.evidence?.confidence ? <Pill tone={KIND_TONE[kind]} className="mt-2">{LINK_KIND_LABEL[kind]}</Pill> : <Pill className="mt-2">No evidence</Pill>}
      </span>
      <span className="text-[13px] text-muted-foreground">
        {link.label || "related"}{typeof score === "number" ? ` · score ${score.toFixed(2)}` : ""}
      </span>
      <span className={cn("break-words text-xs text-muted-foreground", source && "font-mono")}>{source || "No source recorded"}</span>
      {link.evidence?.reason ? <span className="text-xs text-muted-foreground">{link.evidence.reason}</span> : null}
    </li>
  )
}

interface InspectorProps {
  graph: GraphData | null
  nodeId: string | null
  onSelect: (id: string) => void
}

/** The selected node and every relationship it has, with how each one was established. */
export function GraphInspector({ graph, nodeId, onSelect }: InspectorProps) {
  const node = graph?.nodes.find((item) => item.id === nodeId)
  const byId = new Map(graph?.nodes.map((item) => [item.id, item]))
  const links = node ? (graph?.links ?? []).filter((link) => link.source === node.id || link.target === node.id) : []
  const layer = node ? MEMORY_LAYER_CONFIG[node.layer] : null
  return (
    <aside aria-labelledby="graph-node-heading" className="surface flex min-w-0 flex-[1_1_20rem] flex-col gap-3.5 self-start rounded-2xl p-5 lg:max-h-[42rem] lg:overflow-y-auto">
      {node && layer ? (
        <>
          <div className="flex flex-col gap-1">
            <span className={cn("text-xs font-semibold", layer.color)}>{layer.label} node</span>
            <h2 id="graph-node-heading" className="text-lg font-semibold leading-snug">{node.label}</h2>
            {node.full_label && node.full_label !== node.label ? <p className="text-[13px] leading-5 text-muted-foreground">{node.full_label}</p> : null}
            <span className="break-all font-mono text-xs text-muted-foreground">
              {node.id} · {links.length} relationship{links.length === 1 ? "" : "s"}
            </span>
          </div>
          {links.length ? (
            <ul className="m-0 flex list-none flex-col p-0">
              {links.map((link, index) => (
                <Relationship
                  key={`${link.source}-${link.target}-${link.label ?? ""}-${index}`}
                  link={link}
                  other={byId.get(link.source === node.id ? link.target : link.source)}
                  onSelect={onSelect}
                />
              ))}
            </ul>
          ) : <p className="text-sm text-muted-foreground">No relationships recorded for this node.</p>}
          <p className="text-xs leading-5 text-muted-foreground">Inferred and ambiguous links are suggestions, not facts. A score orders links; it is not a probability.</p>
        </>
      ) : (
        <>
          <h2 id="graph-node-heading" className="text-lg font-semibold">Node details</h2>
          <p className="text-sm leading-6 text-muted-foreground">
            Select a node to see its relationships, how each link was established and where the evidence came from.
          </p>
        </>
      )}
    </aside>
  )
}
