"use client"

import Link from "next/link"
import { useEffect, useState } from "react"
import { Activity, ArrowUpRight } from "lucide-react"
import { getRuntimeStatus } from "@/lib/api"
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
  const items = [
    { label: "API connection", value: failed ? "Unavailable" : runtime ? "Connected" : "Checking…", good: !!runtime },
    { label: "Storage", value: storage === true ? "Ready" : storage === false ? "Not ready" : "Unknown", good: storage === true },
    { label: "Embeddings", value: runtime?.embeddingDriverConnected ? "Connected" : embeddings === "failed" ? "Failed" : embeddings === "fallback" || embeddings === "disabled" ? "Keyword fallback" : "Not connected", good: !!runtime?.embeddingDriverConnected },
  ]

  return (
    <section className="rounded-xl border border-border bg-card p-6" aria-labelledby="connection-heading">
      <div className="mb-5 flex items-center justify-between">
        <h2 id="connection-heading" className="font-semibold">Connection status</h2>
        <Activity className="h-4 w-4 text-muted-foreground" />
      </div>
      <dl className="space-y-4">
        {items.map((item) => <div key={item.label} className="flex items-center justify-between gap-3 text-xs">
          <dt className="text-muted-foreground">{item.label}</dt>
          <dd className="flex items-center gap-2 text-right">
            <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${item.good ? "bg-success" : "bg-muted-foreground"}`} />{!runtime && !failed ? "Checking…" : item.value}</dd>
        </div>)}
      </dl>
      {failed && <p className="mt-4 text-xs leading-5 text-muted-foreground">Check your server and sign-in settings in Operations.</p>}
      {runtime?.embeddingStatusMessage && <p className="mt-4 break-words text-xs leading-5 text-muted-foreground">{runtime.embeddingStatusMessage}</p>}
      <Link href={projectHref("/health", repoId)} className="mt-5 flex items-center justify-between border-t border-border pt-4 text-sm font-medium text-primary hover:underline">Open operations<ArrowUpRight className="h-4 w-4" />
      </Link>
    </section>
  )
}
