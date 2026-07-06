"use client"

import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react"
import { getRuntimeStatus } from "@/lib/api"

export const PROJECT_QUERY_PARAM = "repo_id"
export const PROJECT_STORAGE_KEY = "llm-memory-selected-repo-id"

interface SelectedProjectContextValue {
  selectedRepoId: string | null
  selectProject: (repoId: string, persist?: boolean) => void
}

const SelectedProjectContext = createContext<SelectedProjectContextValue | null>(null)

function currentRepoIdFromUrl(): string | null {
  if (typeof window === "undefined") return null
  return new URLSearchParams(window.location.search).get(PROJECT_QUERY_PARAM)
}

// Update the address bar in place, with no navigation and no network request. This is the crux
// of the fix: in a Next static export served by a non-Next server, router.replace() for a
// query-only change triggers an RSC (.txt?_rsc=) fetch the static server can't answer, which the
// App Router recovers from with a full-page reload. history.replaceState mutates the URL without
// any of that, so the selection sticks. window.location.pathname already includes basePath.
function writeRepoIdToUrl(repoId: string) {
  const params = new URLSearchParams(window.location.search)
  params.set(PROJECT_QUERY_PARAM, repoId)
  window.history.replaceState(window.history.state, "", `${window.location.pathname}?${params.toString()}`)
}

export function SelectedProjectProvider({ children }: { children: ReactNode }) {
  // Start null on both server prerender and first client render so hydration matches; the real
  // value is resolved in the mount effect below and gated by `ready` so no consumer renders or
  // fetches with a stale/placeholder repo id.
  const [selectedRepoId, setSelectedRepoId] = useState<string | null>(null)
  const [ready, setReady] = useState(false)
  const initializedRef = useRef(false)

  const selectProject = useCallback((repoId: string, persist = true) => {
    if (!repoId) return
    setSelectedRepoId(repoId)
    if (persist) window.localStorage.setItem(PROJECT_STORAGE_KEY, repoId)
    writeRepoIdToUrl(repoId)
  }, [])

  // One-shot initial resolution: URL query wins (shareable/deep links), then localStorage, then the
  // server's runtime default. Guarded by a ref so it can never re-enter and fight itself — the
  // feedback loop that reverted the selection to "default" is gone.
  useEffect(() => {
    if (initializedRef.current) return
    initializedRef.current = true

    const fromUrl = currentRepoIdFromUrl()
    const stored = window.localStorage.getItem(PROJECT_STORAGE_KEY)
    const initial = fromUrl || stored

    if (initial) {
      // persist=true keeps localStorage authoritative; writeRepoIdToUrl also reflects a
      // localStorage-only value back into the address bar so URLs stay shareable.
      selectProject(initial, true)
      setReady(true)
      return
    }

    let cancelled = false
    getRuntimeStatus()
      .then((runtime) => {
        if (cancelled || !runtime.repoId) return
        selectProject(runtime.repoId, true)
      })
      .catch(() => {
        /* leave selection unset; consumers will surface their own load errors */
      })
      .finally(() => {
        if (!cancelled) setReady(true)
      })
    return () => {
      cancelled = true
    }
  }, [selectProject])

  // Keep React state in sync with browser Back/Forward, which change the URL without a router
  // navigation. Without this the address bar and the rendered project would silently diverge.
  useEffect(() => {
    const onPopState = () => {
      const fromUrl = currentRepoIdFromUrl()
      if (fromUrl) setSelectedRepoId(fromUrl)
    }
    window.addEventListener("popstate", onPopState)
    return () => window.removeEventListener("popstate", onPopState)
  }, [])

  return (
    <SelectedProjectContext.Provider value={{ selectedRepoId, selectProject }}>
      {ready ? children : <ProjectLoadingShell />}
    </SelectedProjectContext.Provider>
  )
}

function ProjectLoadingShell() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-background">
      <div className="h-8 w-8 animate-spin rounded-full border-2 border-muted border-t-foreground" />
    </div>
  )
}

// Drop-in replacement for the previous hook: same name, same signature. Consumers that only read
// the selected project need no changes.
export function useSelectedProjectId(): string | null {
  return useContext(SelectedProjectContext)?.selectedRepoId ?? null
}

// Components that change the selection use this.
export function useSelectProject(): (repoId: string, persist?: boolean) => void {
  const ctx = useContext(SelectedProjectContext)
  if (!ctx) {
    throw new Error("useSelectProject must be used within a SelectedProjectProvider")
  }
  return ctx.selectProject
}

export function projectHref(path: string, repoId?: string | null): string {
  if (!repoId) return path

  const params = new URLSearchParams()
  params.set(PROJECT_QUERY_PARAM, repoId)
  return `${path}?${params.toString()}`
}
