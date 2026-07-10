"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import { useEffect, useRef, useState } from "react"
import {
  Activity,
  Brain,
  ClipboardList,
  LayoutDashboard,
  LogOut,
  Menu,
  Network,
  Search,
  Settings,
  Target,
  X,
} from "lucide-react"
import { ProjectSelector } from "@/components/projects/project-selector"
import { ThemeToggle } from "@/components/ui/theme-toggle"
import {
  AUTH_CHANGED_EVENT,
  clearAuthCredentials,
  getRuntimeStatus,
  hasAuthCredentials,
  isApiError,
} from "@/lib/api"
import { projectHref, useSelectedProjectId } from "@/lib/project-selection"
import { cn } from "@/lib/utils"

const navItems = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/intelligence", label: "Intelligence", icon: ClipboardList },
  { href: "/graph", label: "Memory Graph", icon: Network },
  { href: "/health", label: "Memory Health", icon: Activity },
  { href: "/recall", label: "Recall", icon: Search },
  { href: "/intents", label: "Intents", icon: Target },
  { href: "/settings", label: "Settings", icon: Settings },
]

type SystemHealth = "online" | "degraded" | "offline" | "auth_required"

export function Sidebar() {
  const pathname = usePathname()
  const activePath = pathname === "/dashboard" ? "/" : pathname.replace(/^\/dashboard/, "")
  const selectedRepoId = useSelectedProjectId()
  const [systemHealth, setSystemHealth] = useState<SystemHealth>("online")
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [authenticated, setAuthenticated] = useState(false)
  const closeButtonRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    const checkSystemHealth = async () => {
      try {
        const runtime = await getRuntimeStatus()
        const driverFailed =
          runtime.embeddingDriverStatus === "failed" ||
          runtime.embeddingDriverStatus === "fallback"
        setSystemHealth(runtime.storageReady === false || driverFailed ? "degraded" : "online")
      } catch (error) {
        setSystemHealth(isApiError(error) && error.status === 401 ? "auth_required" : "offline")
      }
    }

    void checkSystemHealth()
    const interval = window.setInterval(checkSystemHealth, 30000)
    return () => window.clearInterval(interval)
  }, [])

  useEffect(() => {
    const updateAuthState = () => setAuthenticated(hasAuthCredentials())
    updateAuthState()
    window.addEventListener(AUTH_CHANGED_EVENT, updateAuthState)
    return () => window.removeEventListener(AUTH_CHANGED_EVENT, updateAuthState)
  }, [])

  useEffect(() => {
    if (!drawerOpen) return
    closeButtonRef.current?.focus()
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setDrawerOpen(false)
    }
    document.body.style.overflow = "hidden"
    window.addEventListener("keydown", handleKeyDown)
    return () => {
      document.body.style.overflow = ""
      window.removeEventListener("keydown", handleKeyDown)
    }
  }, [drawerOpen])

  useEffect(() => setDrawerOpen(false), [pathname])

  const systemIndicator = {
    online: { color: "bg-success", label: "System Online" },
    degraded: { color: "bg-intent", label: "System Degraded" },
    offline: { color: "bg-error", label: "System Offline" },
    auth_required: { color: "bg-intent", label: "Authentication Required" },
  }[systemHealth]

  const renderNavigation = () =>
    navItems.map((item) => {
      const isActive = activePath === item.href
      return (
        <Link
          key={item.href}
          href={projectHref(item.href, selectedRepoId)}
          className={cn(
            "flex items-center gap-3 rounded-md px-4 py-2.5 text-sm font-medium transition-colors",
            isActive
              ? "bg-primary text-primary-foreground"
              : "text-muted-foreground hover:bg-secondary hover:text-foreground",
          )}
        >
          <item.icon className="h-5 w-5 shrink-0" />
          <span>{item.label}</span>
        </Link>
      )
    })

  const handleClearCredentials = () => {
    clearAuthCredentials()
    setAuthenticated(false)
    setSystemHealth("auth_required")
  }

  return (
    <>
      <header className="sticky top-0 z-40 flex h-16 items-center justify-between border-b border-border bg-card px-4 md:hidden">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-md bg-primary">
            <Brain className="h-5 w-5 text-primary-foreground" />
          </div>
          <div>
            <p className="font-semibold text-foreground">LLM Memory</p>
            <p className="text-xs text-muted-foreground">Dashboard</p>
          </div>
        </div>
        <button
          type="button"
          aria-label="Open navigation menu"
          aria-expanded={drawerOpen}
          onClick={() => setDrawerOpen(true)}
          className="flex h-10 w-10 items-center justify-center rounded-md border border-border text-foreground hover:bg-secondary"
        >
          <Menu className="h-5 w-5" />
        </button>
      </header>

      <aside className="fixed left-0 top-0 z-40 hidden h-screen w-60 flex-col border-r border-border bg-card md:flex">
        <div className="flex items-center gap-3 border-b border-border px-6 py-5">
          <div className="flex h-10 w-10 items-center justify-center rounded-md bg-primary">
            <Brain className="h-5 w-5 text-primary-foreground" />
          </div>
          <div>
            <h1 className="font-semibold text-foreground">LLM Memory</h1>
            <p className="text-xs text-muted-foreground">Dashboard</p>
          </div>
        </div>
        <nav className="flex-1 space-y-1 px-3 py-4" aria-label="Primary navigation">
          {renderNavigation()}
        </nav>
        <div className="space-y-3 border-t border-border px-3 py-4">
          <ProjectSelector />
          <div className="flex items-center justify-between px-4">
            <span className="text-sm text-muted-foreground">Theme</span>
            <ThemeToggle />
          </div>
          <div className="flex items-center gap-2 px-4 py-2">
            <span className={cn("h-2 w-2 rounded-full", systemIndicator.color)} />
            <span className="text-xs text-muted-foreground">{systemIndicator.label}</span>
          </div>
        </div>
      </aside>

      {drawerOpen ? (
        <div className="fixed inset-0 z-50 md:hidden">
          <button
            type="button"
            aria-label="Close navigation menu"
            className="absolute inset-0 bg-black/45"
            onClick={() => setDrawerOpen(false)}
          />
          <section
            role="dialog"
            aria-modal="true"
            aria-label="Navigation menu"
            className="absolute inset-y-0 right-0 flex w-[min(88vw,22rem)] flex-col border-l border-border bg-card shadow-xl"
          >
            <div className="flex h-16 items-center justify-between border-b border-border px-4">
              <span className="font-semibold text-foreground">Navigation</span>
              <button
                ref={closeButtonRef}
                type="button"
                aria-label="Close navigation menu"
                onClick={() => setDrawerOpen(false)}
                className="flex h-10 w-10 items-center justify-center rounded-md text-foreground hover:bg-secondary"
              >
                <X className="h-5 w-5" />
              </button>
            </div>
            <nav className="flex-1 space-y-1 overflow-y-auto px-3 py-4" aria-label="Mobile navigation">
              {renderNavigation()}
            </nav>
            <div className="space-y-3 border-t border-border px-3 py-4">
              <ProjectSelector />
              <div className="flex items-center justify-between px-4">
                <span className="text-sm text-muted-foreground">Theme</span>
                <ThemeToggle />
              </div>
              <div className="flex items-center justify-between gap-3 px-4 py-2">
                <div className="flex min-w-0 items-center gap-2">
                  <span className={cn("h-2 w-2 shrink-0 rounded-full", systemIndicator.color)} />
                  <span className="truncate text-xs text-muted-foreground">{systemIndicator.label}</span>
                </div>
                {authenticated ? (
                  <button
                    type="button"
                    onClick={handleClearCredentials}
                    className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-secondary hover:text-foreground"
                    aria-label="Clear authentication credentials"
                    title="Clear authentication credentials"
                  >
                    <LogOut className="h-4 w-4" />
                  </button>
                ) : null}
              </div>
            </div>
          </section>
        </div>
      ) : null}
    </>
  )
}
