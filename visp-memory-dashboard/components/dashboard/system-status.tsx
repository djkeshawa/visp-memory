"use client"

import Link from "next/link"
import { useEffect, useState } from "react"
import { Panel, StatusDot } from "@/components/strata/primitives"
import { getRuntimeStatus } from "@/lib/api"
import type { TrustTone } from "@/lib/memory-trust"
import { projectHref, useSelectedProjectId } from "@/lib/project-selection"
import type { RuntimeStatus } from "@/lib/types"

export function SystemStatus() {
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
    const interval = window.setInterval(check, 30000)
    return () => { active = false; window.clearInterval(interval) }
  }, [])

  const storage = runtime?.storageReady
  const embeddings = runtime?.embeddingDriverStatus
  const fallback = embeddings === "fallback" || embeddings === "disabled"
  const items: { label: string; value: string; tone: TrustTone; hollow?: boolean }[] = [
    { label: "API connection", value: failed ? "Unavailable" : runtime ? "Connected" : "Checking…", tone: runtime ? "success" : failed ? "danger" : "neutral" },
    { label: "Storage", value: storage === true ? "Ready" : storage === false ? "Not ready" : "Unknown", tone: storage === true ? "success" : storage === false ? "danger" : "neutral" },
    { label: "Embeddings", value: runtime?.embeddingDriverConnected ? "Connected" : embeddings === "failed" ? "Failed" : fallback ? "Keyword fallback" : "Not connected", tone: runtime?.embeddingDriverConnected ? "success" : embeddings === "failed" ? "danger" : "neutral", hollow: !runtime?.embeddingDriverConnected && fallback },
  ]
  const checking = !runtime && !failed

  return (
    <Panel title="Connection status" titleId="connection-heading">
      <dl className="space-y-3">
        {items.map((item) => (
          <div key={item.label} className="flex items-center justify-between gap-3 text-[13px]">
            <dt className="text-muted-foreground">{item.label}</dt>
            <dd className="flex items-center gap-2 text-right"><StatusDot tone={checking ? "neutral" : item.tone} hollow={!checking && item.hollow} />{checking ? "Checking…" : item.value}</dd>
          </div>
        ))}
      </dl>
      {failed && <p className="mt-4 text-xs leading-5 text-muted-foreground">Check your server and sign-in settings in Operations.</p>}
      {runtime?.embeddingStatusMessage && <p className="mt-4 break-words text-xs leading-5 text-muted-foreground">{runtime.embeddingStatusMessage}</p>}
      <Link href={projectHref("/health", repoId)} className="mt-4 inline-block text-[13px] font-medium text-highlight hover:underline">Open operations<span aria-hidden="true"> →</span></Link>
    </Panel>
  )
}
