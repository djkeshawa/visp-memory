import { Pill, StatusDot } from "@/components/strata/primitives"
import { Button } from "@/components/ui/button"
import type { ProviderDiagnostic } from "@/lib/types"
import { PROVIDER_STATUS, formatLastChecked } from "./format"

export function ProviderList({ providers, testing, onTest }: {
  providers: ProviderDiagnostic[]
  testing: string | null
  onTest: (provider: string) => void
}) {
  return (
    <section aria-labelledby="providers-heading" className="space-y-3">
      <h2 id="providers-heading" className="text-lg font-semibold">Provider diagnostics</h2>
      {providers.map((provider) => {
        const status = PROVIDER_STATUS[provider.status]
        const busy = testing === provider.provider
        return (
          <article key={provider.provider} className="surface rounded-2xl p-5 sm:p-6">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="flex flex-wrap items-center gap-2">
                <h3 className="text-base font-semibold">{provider.provider}</h3>
                <Pill tone={status.tone}><StatusDot tone={status.tone} />{status.label}</Pill>
              </div>
              <Button variant="outline" className="h-10" onClick={() => onTest(provider.provider)} disabled={Boolean(testing)}>
                {busy ? "Testing..." : "Test"}
              </Button>
            </div>
            <dl className="mt-4 grid gap-3 text-sm sm:grid-cols-3">
              <div><dt className="text-xs text-muted-foreground">Model</dt><dd className="mt-0.5 font-medium">{provider.model || "Not set"}</dd></div>
              <div><dt className="text-xs text-muted-foreground">Dimension</dt><dd className="mt-0.5 font-medium">{typeof provider.dimension === "number" ? provider.dimension : "Not set"}</dd></div>
              <div><dt className="text-xs text-muted-foreground">Last checked</dt><dd className="mt-0.5 font-medium">{formatLastChecked(provider.lastChecked)}</dd></div>
            </dl>
            {provider.message ? <p className="mt-3 text-sm text-muted-foreground">{provider.message}</p> : null}
          </article>
        )
      })}
    </section>
  )
}
