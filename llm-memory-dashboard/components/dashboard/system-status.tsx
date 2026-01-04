"use client"

import { useEffect, useState } from "react"
import type { SystemStatus as SystemStatusType } from "@/lib/types"
import { cn } from "@/lib/utils"
import { getStats } from "@/lib/api"
import { mockSystemStatus } from "@/lib/mock-data"

interface SystemStatusProps {
  status: SystemStatusType // Keep this prop for initial/fallback
}

const statusConfig = {
  online: { color: "bg-success", label: "Online" },
  offline: { color: "bg-error", label: "Offline" },
  ready: { color: "bg-success", label: "Ready" },
  syncing: { color: "bg-intent", label: "Syncing" },
  active: { color: "bg-success", label: "Active" },
  inactive: { color: "bg-muted-foreground", label: "Inactive" },
}

export function SystemStatus({ status: initialStatus }: SystemStatusProps) {
  const [status, setStatus] = useState<SystemStatusType>(initialStatus)

  useEffect(() => {
    checkStatus()
    const interval = setInterval(checkStatus, 30000)
    return () => clearInterval(interval)
  }, [])

  const checkStatus = async () => {
    try {
      await getStats()
      setStatus({
        apiServer: "online",
        vectorDatabase: "ready", // Inferred
        embeddings: "active"    // Inferred
      })
    } catch (e) {
      setStatus({
        apiServer: "offline",
        vectorDatabase: "offline",
        embeddings: "inactive"
      })
    }
  }

  const items = [
    { label: "API Server", status: status.apiServer },
    { label: "Vector Database", status: status.vectorDatabase },
    { label: "Embeddings", status: status.embeddings },
  ]

  return (
    <div className="glass rounded-xl p-5">
      <h3 className="text-sm font-semibold text-foreground mb-4">System Status</h3>
      <div className="space-y-3">
        {items.map((item) => {
          const config = statusConfig[item.status]
          return (
            <div key={item.label} className="flex items-center justify-between">
              <span className="text-sm text-muted-foreground">{item.label}</span>
              <div className="flex items-center gap-2">
                <div className={cn("h-2 w-2 rounded-full", config.color)} />
                <span className="text-sm font-medium text-foreground">{config.label}</span>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
