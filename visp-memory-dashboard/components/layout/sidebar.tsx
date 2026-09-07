"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import { useEffect, useRef, useState } from "react"
import { Brain, LogOut, Menu, UserRound, X } from "lucide-react"
import { Navigation } from "./navigation"
import { ProjectSelector } from "@/components/projects/project-selector"
import { ThemeToggle } from "@/components/ui/theme-toggle"
import {
  AUTH_CHANGED_EVENT,
  getCurrentAccount,
  getRuntimeStatus,
  isApiError,
  logout,
} from "@/lib/api"
import type { AuthUser } from "@/lib/types"
import { projectHref, useSelectedProjectId } from "@/lib/project-selection"
import { cn } from "@/lib/utils"

type SystemHealth = "checking" | "online" | "degraded" | "offline" | "auth_required"

export function Sidebar() {
  const pathname = usePathname()
  const activePath = pathname === "/dashboard" ? "/" : pathname.replace(/^\/dashboard/, "")
  const selectedRepoId = useSelectedProjectId()
  const [systemHealth, setSystemHealth] = useState<SystemHealth>("checking")
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [authenticated, setAuthenticated] = useState(false)
  const [account, setAccount] = useState<AuthUser | null>(null)
  const drawerRef = useRef<HTMLElement>(null)
  const openButtonRef = useRef<HTMLButtonElement>(null)
  const closeButtonRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    const checkSystemHealth = async () => {
      try {
        const runtime = await getRuntimeStatus()
        // "fallback" and "disabled" mean recall is running on keyword search rather
        // than vectors. That is the default install and a supported configuration, not
        // a fault -- every backend routes around noop embeddings deliberately. Flagging
        // it as degraded showed "System Degraded" to every user on the lean install.
        // Only a driver that genuinely failed, or storage that is not ready, is degraded.
        const driverFailed = runtime.embeddingDriverStatus === "failed"
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
    const updateAuthState = async () => {
      try {
        const session = await getCurrentAccount()
        setAccount(session.user)
        setAuthenticated(true)
      } catch {
        setAccount(null)
        setAuthenticated(false)
      }
    }
    void updateAuthState()
    window.addEventListener(AUTH_CHANGED_EVENT, updateAuthState)
    return () => window.removeEventListener(AUTH_CHANGED_EVENT, updateAuthState)
  }, [])

  useEffect(() => {
    if (!drawerOpen) return
    closeButtonRef.current?.focus()
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setDrawerOpen(false)
      if (event.key !== "Tab") return
      const controls = drawerRef.current?.querySelectorAll<HTMLElement>(
        'a[href], button:not(:disabled), select:not(:disabled), [tabindex="0"]',
      )
      if (!controls?.length) return
      const first = controls[0]
      const last = controls[controls.length - 1]
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }
    document.body.style.overflow = "hidden"
    window.addEventListener("keydown", handleKeyDown)
    return () => {
      document.body.style.overflow = ""
      window.removeEventListener("keydown", handleKeyDown)
      openButtonRef.current?.focus()
    }
  }, [drawerOpen])

  useEffect(() => setDrawerOpen(false), [pathname])

  const systemIndicator = {
    checking: { color: "bg-muted-foreground", label: "Checking connection" },
    online: { color: "bg-success", label: "System Online" },
    degraded: { color: "bg-intent", label: "System Degraded" },
    offline: { color: "bg-error", label: "System Offline" },
    auth_required: { color: "bg-intent", label: "Authentication Required" },
  }[systemHealth]

  const handleLogout = async () => {
    try {
      await logout()
    } finally {
      setAccount(null)
      setAuthenticated(false)
      setSystemHealth("auth_required")
      window.location.assign("/dashboard/auth")
    }
  }

  return (
    <>
      <header className="sticky top-0 z-40 flex h-16 items-center justify-between border-b border-border bg-sidebar/95 px-4 backdrop-blur-xl md:hidden">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl border border-border bg-card">
            <Brain className="h-5 w-5 text-foreground" />
          </div>
          <div>
            <p className="font-semibold text-foreground">Visp Memory</p>
            <p className="text-xs text-muted-foreground">Project knowledge</p>
          </div>
        </div>
        <button
          type="button"
          ref={openButtonRef}
          aria-label="Open navigation menu"
          aria-expanded={drawerOpen}
          onClick={() => setDrawerOpen(true)}
          className="flex h-10 w-10 items-center justify-center rounded-md border border-border text-foreground hover:bg-secondary"
        >
          <Menu className="h-5 w-5" />
        </button>
      </header>

      <aside className="fixed left-0 top-0 z-40 hidden h-screen w-60 flex-col border-r border-border/70 bg-sidebar md:flex">
        <div className="flex items-center gap-3 px-6 pb-4 pt-7">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl border border-border bg-card">
            <Brain className="h-5 w-5 text-foreground" />
          </div>
          <div>
            <p className="font-semibold text-foreground">Visp Memory</p>
            <p className="text-xs text-muted-foreground">Project knowledge</p>
          </div>
        </div>
        <div className="pb-1 pt-2"><ProjectSelector /></div>
        <nav className="workspace-nav flex-1 space-y-1 overflow-y-auto px-3 pb-4" aria-label="Primary navigation">
          <Navigation activePath={activePath} repoId={selectedRepoId} />
        </nav>
        <div className="space-y-2 border-t border-border/70 px-3 py-3">
          <div className="flex items-center justify-between px-4">
            <span className="text-sm text-muted-foreground">Theme</span>
            <ThemeToggle />
          </div>
          {systemHealth === "auth_required" ? (
            <Link
              href={projectHref("/auth", selectedRepoId)}
              className="flex items-center gap-2 rounded-md px-4 py-2 text-xs text-intent hover:bg-secondary"
            >
              <span className={cn("h-2 w-2 rounded-full", systemIndicator.color)} />
              <span>{systemIndicator.label}</span>
            </Link>
          ) : (
            <div className="flex items-center gap-2 px-4 py-2">
              <span className={cn("h-2 w-2 rounded-full", systemIndicator.color)} />
              <span className="text-xs text-muted-foreground">{systemIndicator.label}</span>
            </div>
          )}
          {authenticated && account ? (
            <div className="flex items-center gap-3 px-3 pt-2">
              <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-secondary text-foreground">
                <UserRound className="h-4 w-4" />
              </div>
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium text-foreground">{account.displayName || account.username}</p>
                <p className="text-xs capitalize text-muted-foreground">{account.role}</p>
              </div>
              <button type="button" onClick={() => void handleLogout()} className="flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground hover:bg-secondary hover:text-foreground" aria-label="Sign out" title="Sign out">
                <LogOut className="h-4 w-4" />
              </button>
            </div>
          ) : null}
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
            ref={drawerRef}
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
            <nav className="workspace-nav flex-1 space-y-1 overflow-y-auto px-3 pb-4" aria-label="Mobile navigation">
              <Navigation activePath={activePath} repoId={selectedRepoId} />
            </nav>
            <div className="space-y-2 border-t border-border/70 px-3 py-3">
              <ProjectSelector />
              <div className="flex items-center justify-between px-4">
                <span className="text-sm text-muted-foreground">Theme</span>
                <ThemeToggle />
              </div>
              <div className="flex items-center justify-between gap-3 px-4 py-2">
                {systemHealth === "auth_required" ? (
                  <Link
                    href={projectHref("/auth", selectedRepoId)}
                    className="flex min-w-0 items-center gap-2 text-intent"
                  >
                    <span className={cn("h-2 w-2 shrink-0 rounded-full", systemIndicator.color)} />
                    <span className="truncate text-xs">{systemIndicator.label}</span>
                  </Link>
                ) : (
                  <div className="flex min-w-0 items-center gap-2">
                    <span className={cn("h-2 w-2 shrink-0 rounded-full", systemIndicator.color)} />
                    <span className="truncate text-xs text-muted-foreground">{systemIndicator.label}</span>
                  </div>
                )}
                {authenticated ? (
                  <button
                    type="button"
                    onClick={() => void handleLogout()}
                    className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-secondary hover:text-foreground"
                    aria-label="Sign out"
                    title="Sign out"
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
