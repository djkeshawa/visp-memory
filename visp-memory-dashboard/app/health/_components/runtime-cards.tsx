"use client"

import Link from "next/link"
import { useEffect, useState } from "react"
import { StatusDot } from "@/components/strata/primitives"
import { getRuntimeStatus } from "@/lib/api"
import { projectHref, useSelectedProjectId } from "@/lib/project-selection"
import type { TrustTone } from "@/lib/memory-trust"
import type { RuntimeStatus } from "@/lib/types"

const POLL_MS = 30_000

interface CardState { label: string; value: string; tone: TrustTone; hollow?: boolean; detail?: string }

function describe(runtime: RuntimeStatus | null, failed: boolean): CardState[] {
  const checking = !runtime && !failed
  const storage = runtime?.storageReady
  const driver = runtime?.embeddingDriverStatus
  const storageDetail = [runtime?.storageBackend, runtime?.storageMode].filter(Boolean).join(" · ")
  const keywordFallback = driver === "fallback" || driver === "disabled" || driver === "not_configured"
  return [
    { label: "API", value: failed ? "Unavailable" : checking ? "Checking…" : "Connected", tone: failed ? "danger" : runtime ? "success" : "neutral", detail: runtime?.version ? `version ${runtime.version}` : undefined },
    { label: "Storage", value: checking ? "Checking…" : storage === true ? "Ready" : storage === false ? "Not ready" : "Unknown", tone: storage === true ? "success" : storage === false ? "danger" : "neutral", detail: storageDetail || undefined },
    {
      label: "Embeddings",
      value: checking ? "Checking…" : failed ? "Unknown" : runtime?.embeddingDriverConnected ? "Connected" : driver === "failed" ? "Failed" : keywordFallback ? "Keyword fallback" : "Not connected",
      tone: !runtime ? "neutral" : runtime.embeddingDriverConnected ? "success" : driver === "failed" ? "danger" : "warning",
      hollow: !!runtime && !runtime.embeddingDriverConnected && driver !== "failed",
      detail: runtime?.embeddingModel,
    },
  ]
}

export function RuntimeCards() {
  const repoId = useSelectedProjectId()
  const [runtime, setRuntime] = useState<RuntimeStatus | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let active = true
    const check = async () => {
      try {
        const result = await getRuntimeStatus()
        if (active) { setRuntime(result); setFailed(false) }
      } catch {
        if (active) { setRuntime(null); setFailed(true) }
      }
    }
    void check()
    const interval = window.setInterval(check, POLL_MS)
    return () => { active = false; window.clearInterval(interval) }
  }, [])

  const cards = describe(runtime, failed)
  const embeddingNote = runtime?.embeddingStatusMessage
  return (
    <section aria-label="Runtime" className="grid grid-cols-[repeat(auto-fit,minmax(14rem,1fr))] gap-3">
      {cards.map((card) => (
        <div key={card.label} className="surface flex min-w-0 flex-col gap-1 rounded-2xl px-[18px] py-4">
          <span className="flex items-center gap-2 text-[13px] text-muted-foreground"><StatusDot tone={card.tone} hollow={card.hollow} />{card.label}</span>
          <span className="text-[17px] font-semibold">{card.value}</span>
          {card.detail ? <span className="break-words text-xs text-muted-foreground">{card.detail}</span> : null}
          {card.label === "Embeddings" && card.hollow ? (
            <>
              {embeddingNote ? <span className="break-words text-xs text-muted-foreground">{embeddingNote}</span> : null}
              <Link href={projectHref("/settings", repoId)} className="text-xs text-highlight hover:underline">Set up semantic search →</Link>
            </>
          ) : null}
        </div>
      ))}
    </section>
  )
}
