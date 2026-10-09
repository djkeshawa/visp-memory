"use client"

import Link from "next/link"
import { Suspense, useEffect, useState } from "react"
import { StatusDot } from "@/components/strata/primitives"
import { StrataMark } from "@/components/strata/layer-glyph"
import { Button } from "@/components/ui/button"
import { dashboardReturnPath } from "@/lib/auth-navigation"
import { describeApiError, getEmbeddingIndexStatus, getRuntimeStatus } from "@/lib/api"
import { useSelectedProjectId } from "@/lib/project-selection"
import type { EmbeddingIndexStatus, RuntimeStatus } from "@/lib/types"
import { ChoiceCards } from "./_components/choice-cards"
import { type ChoiceKey } from "./_components/choices"
import { ConfigPanel } from "./_components/config-panel"
import { StepChips } from "./_components/step-chips"

function SetupContent() {
  const repoId = useSelectedProjectId()
  const [choice, setChoice] = useState<ChoiceKey>("keyword")
  const [runtime, setRuntime] = useState<RuntimeStatus | null>(null)
  const [index, setIndex] = useState<EmbeddingIndexStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    let active = true
    setLoading(true)
    setRuntime(null)
    setIndex(null)
    Promise.all([getRuntimeStatus(), getEmbeddingIndexStatus(repoId)])
      .then(([server, embedding]) => { if (active) { setRuntime(server); setIndex(embedding); setError(null) } })
      .catch((failure) => { if (active) setError(describeApiError(failure)) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [repoId, attempt])
  const semantic = runtime?.embeddingDriverConnected && index?.status === "available"
  const statusText = loading ? "Checking search setup…" : error ? "Search setup could not be checked" : semantic ? "Semantic search is available" : "Keyword search is available"
  return (
    <div className="mx-auto flex max-w-[840px] flex-col gap-7">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <span className="flex items-center gap-2.5"><StrataMark /><span className="text-[15px] font-semibold">visp memory</span></span>
        <StepChips hasProject={Boolean(repoId)} searchReady={Boolean(semantic)} />
      </div>
      <header className="flex flex-col gap-2">
        <h1 className="text-4xl">Choose how to find your memories</h1>
        <p className="max-w-[620px] text-base leading-7 text-muted-foreground">Start with keyword search, or connect a model for meaning-based matches. Exploring options here does not change your server. You can come back from Settings.</p>
      </header>
      <ChoiceCards value={choice} onChange={setChoice} />
      <ConfigPanel choice={choice} />
      <section aria-label="Current search setup" className="surface flex flex-col gap-3 rounded-2xl p-4 sm:px-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p role="status" className="flex items-center gap-2.5 text-sm">
            <StatusDot tone={error ? "danger" : semantic ? "success" : "warning"} hollow={!semantic && !error} />
            {statusText}
          </p>
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" className="h-11" disabled={loading} onClick={() => setAttempt((value) => value + 1)}>Check again</Button>
            <Button className="h-11 px-5" onClick={() => window.location.replace(dashboardReturnPath())}>Continue to workspace</Button>
          </div>
        </div>
        {error ? <p role="alert" className="text-sm text-destructive">{error}</p> : null}
        {index ? <p className="text-xs leading-5 text-muted-foreground">{index.message}</p> : null}
        {runtime?.embeddingDriverConnected && index?.status !== "available" ? <p className="text-sm">Your provider is connected, but this store still needs a vector index before it can search by meaning.</p> : null}
      </section>
      <p className="text-sm"><Link href="/projects" className="font-medium text-highlight underline underline-offset-4">Add or select a project</Link></p>
    </div>
  )
}

export default function SetupPage() {
  return <Suspense fallback={null}><SetupContent /></Suspense>
}
