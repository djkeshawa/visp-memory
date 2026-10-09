import { Button } from "@/components/ui/button"

interface TokenCreatedProps {
  name: string | null
  secret: string
  copied: boolean
  onCopy: () => void
  onDone: () => void
}

/** The one-time secret callout. The token is never retrievable again after this is dismissed. */
export function TokenCreated({ name, secret, copied, onCopy, onDone }: TokenCreatedProps) {
  return (
    <section aria-labelledby="new-token-heading" className="flex flex-col gap-3 rounded-2xl border border-success/40 bg-success/10 px-5 py-4 sm:px-6">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="new-token-heading" className="text-[15px] font-semibold">Token created{name ? ` · ${name}` : ""}</h2>
        <span className="text-[13px] text-muted-foreground">This secret is shown once. Add it to your API or MCP client now.</span>
      </div>
      <div className="flex flex-wrap gap-2">
        <code className="flex h-11 min-w-0 flex-[1_1_20rem] items-center overflow-x-auto whitespace-nowrap rounded-xl border border-success/40 bg-background px-3.5 font-mono text-[13px]">{secret}</code>
        <Button onClick={onCopy} className="h-11 font-semibold">
          {copied ? "Copied" : "Copy token"}
        </Button>
        <Button variant="outline" onClick={onDone} className="h-11">Done</Button>
      </div>
    </section>
  )
}
