"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import { useEffect, useState } from "react"
import { Activity, Brain, ClipboardList, LayoutDashboard, Network, Search, Settings, Target } from "lucide-react"
import { cn } from "@/lib/utils"
import { ThemeToggle } from "@/components/ui/theme-toggle"
import { ProjectSelector } from "@/components/projects/project-selector"
import { projectHref, useSelectedProjectId } from "@/lib/project-selection"
import { getRuntimeStatus } from "@/lib/api"

const navItems = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/intelligence", label: "Intelligence", icon: ClipboardList },
  { href: "/graph", label: "Memory Graph", icon: Network },
  { href: "/health", label: "Memory Health", icon: Activity },
  { href: "/recall", label: "Recall", icon: Search },
  { href: "/intents", label: "Intents", icon: Target },
  { href: "/settings", label: "Settings", icon: Settings },
]

export function Sidebar() {
  const pathname = usePathname()
  const activePath = pathname === "/dashboard" ? "/" : pathname.replace(/^\/dashboard/, "")
  const selectedRepoId = useSelectedProjectId()
  const [systemHealth, setSystemHealth] = useState<"online" | "degraded" | "offline">("online")

  useEffect(() => {
    checkSystemHealth()
    const interval = setInterval(checkSystemHealth, 30000)
    return () => clearInterval(interval)
  }, [])

  const checkSystemHealth = async () => {
    try {
      const runtime = await getRuntimeStatus()
      const driverFailed =
        runtime.embeddingDriverStatus === "failed" ||
        runtime.embeddingDriverStatus === "fallback"
      setSystemHealth(runtime.storageReady === false || driverFailed ? "degraded" : "online")
    } catch {
      setSystemHealth("offline")
    }
  }

  const systemIndicator = {
    online: { color: "bg-success", label: "System Online" },
    degraded: { color: "bg-intent", label: "System Degraded" },
    offline: { color: "bg-error", label: "System Offline" },
  }[systemHealth]

  return (
    <aside className="sticky top-0 z-40 flex w-full flex-col border-b border-border bg-card md:fixed md:left-0 md:top-0 md:h-screen md:w-60 md:border-b-0 md:border-r">
      {/* Logo */}
      <div className="flex items-center gap-3 border-b border-border px-4 py-4 md:px-6 md:py-5">
        <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-blue-500 to-indigo-600">
          <Brain className="h-5 w-5 text-white" />
        </div>
        <div>
          <h1 className="font-semibold text-foreground">LLM Memory</h1>
          <p className="text-xs text-muted-foreground">Dashboard</p>
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex gap-1 overflow-x-auto px-3 py-3 md:block md:flex-1 md:space-y-1 md:overflow-visible md:py-4">
        {navItems.map((item) => {
          const isActive = activePath === item.href
          return (
            <Link
              key={item.href}
              href={projectHref(item.href, selectedRepoId)}
              className={cn(
                "flex shrink-0 items-center gap-2 rounded-lg px-3 py-2.5 text-sm font-medium transition-all duration-200 md:gap-3 md:px-4",
                isActive
                  ? "bg-gradient-to-r from-blue-500 to-indigo-600 text-white shadow-md"
                  : "text-muted-foreground hover:bg-secondary hover:text-foreground",
              )}
            >
              <item.icon className="h-5 w-5" />
              {item.label}
            </Link>
          )
        })}
      </nav>

      {/* Bottom Section */}
      <div className="hidden space-y-3 border-t border-border px-3 py-4 md:block">
        <ProjectSelector />

        <div className="flex items-center justify-between px-4">
          <span className="text-sm text-muted-foreground">Theme</span>
          <ThemeToggle />
        </div>

        {/* Status Indicator */}
        <div className="flex items-center gap-2 px-4 py-2">
          <div className={cn("h-2 w-2 rounded-full animate-pulse", systemIndicator.color)} />
          <span className="text-xs text-muted-foreground">{systemIndicator.label}</span>
        </div>
      </div>
    </aside>
  )
}
