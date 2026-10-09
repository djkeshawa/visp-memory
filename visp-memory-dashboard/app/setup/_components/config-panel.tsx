import Link from "next/link"
import { Panel } from "@/components/strata/primitives"
import { CHOICES, type ChoiceKey } from "./choices"

export function ConfigPanel({ choice }: { choice: ChoiceKey }) {
  const item = CHOICES[choice]
  return (
    <Panel title={`Server settings for ${item.title}`} titleId="config-heading" aside={<span className="text-xs text-muted-foreground">Add to the server environment, then restart it</span>} bodyClassName="space-y-3">
      {choice === "keyword" ? (
        <p className="text-sm leading-6 text-muted-foreground">Keyword search needs no setup. Exploring these options does not change your server configuration.</p>
      ) : (
        <p className="text-sm leading-6 text-muted-foreground">Keep provider keys on the server. This page does not save or expose credentials.</p>
      )}
      <pre className="well overflow-x-auto whitespace-pre-wrap break-words rounded-xl border border-border p-4 font-mono text-[13px] leading-7">{item.config}</pre>
      {item.note ? <p className="text-sm leading-6 text-muted-foreground">{item.note}</p> : null}
      {choice !== "keyword" ? (
        <>
          <p className="text-sm leading-6 text-muted-foreground">SQLite also requires the optional Chroma vector index. After configuration, use Settings to inspect the index and rebuild embeddings for existing memories.</p>
          <Link className="inline-flex min-h-10 items-center text-sm font-medium text-highlight underline underline-offset-4" href="/settings">Open embedding diagnostics</Link>
        </>
      ) : null}
    </Panel>
  )
}
