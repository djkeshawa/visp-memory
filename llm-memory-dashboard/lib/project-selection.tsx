"use client"

import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react"
import { describeApiError, getProjectScopes, getRuntimeStatus } from "@/lib/api"
import type { ProjectScope } from "@/lib/types"

export const PROJECT_QUERY_PARAM = "repo_id"
export const PROJECT_STORAGE_KEY = "llm-memory-selected-repo-id"

interface SelectedProjectContextValue {
  selectedRepoId: string | null
  projects: ProjectScope[]
  loadError: string | null
  selectProject: (repoId: string, persist?: boolean) => void
}

const SelectedProjectContext = createContext<SelectedProjectContextValue | null>(null)

function currentRepoIdFromUrl(): string | null {
  if (typeof window === "undefined") return null
  return new URLSearchParams(window.location.search).get(PROJECT_QUERY_PARAM)
}

// localStorage can throw (private mode, disabled by policy, quota). Never let a storage failure
// break selection or, worse, wedge the readiness gate.
function readStoredRepoId(): string | null {
  try {
    return window.localStorage.getItem(PROJECT_STORAGE_KEY)
  } catch {
    return null
  }
}

function writeStoredRepoId(repoId: string) {
  try {
    window.localStorage.setItem(PROJECT_STORAGE_KEY, repoId)
  } catch {
    /* persistence unavailable; keep the in-memory selection */
  }
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
  // value is resolved in the mount effect and gated by `ready`.
  const [selectedRepoId, setSelectedRepoId] = useState<string | null>(null)
  const [projects, setProjects] = useState<ProjectScope[]>([])
  const [loadError, setLoadError] = useState<string | null>(null)
  const [ready, setReady] = useState(false)
  const initializedRef = useRef(false)
  // Mirror of selectedRepoId readable synchronously from event handlers (popstate) without
  // resubscribing an effect on every change.
  const selectedRef = useRef<string | null>(null)

  const applySelection = useCallback((repoId: string | null) => {
    selectedRef.current = repoId
    setSelectedRepoId(repoId)
  }, [])

  const selectProject = useCallback(
    (repoId: string, persist = true) => {
      if (!repoId) return
      applySelection(repoId)
      if (persist) writeStoredRepoId(repoId)
      writeRepoIdToUrl(repoId)
    },
    [applySelection],
  )

  // One-shot initialization. Guarded by a ref (not by any state/URL value) so it can never
  // re-enter and fight itself, and StrictMode's double-invoke in dev can't wedge it (there is no
  // cancel-cleanup that could suppress the final setReady).
  useEffect(() => {
    if (initializedRef.current) return
    initializedRef.current = true

    const fromUrl = currentRepoIdFromUrl()
    const stored = readStoredRepoId()

    // Fast path: if the URL or localStorage already names a repo, show the app immediately with
    // it so there is no loading flash on a warm load. The value is validated/corrected below once
    // the scope list is known.
    if (fromUrl || stored) {
      selectProject((fromUrl || stored) as string, true)
      setReady(true)
    }

    void (async () => {
      let scopes: ProjectScope[] = []
      let runtimeRepoId: string | null = null
      try {
        const [scopeList, runtime] = await Promise.all([getProjectScopes(), getRuntimeStatus()])
        scopes = scopeList
        runtimeRepoId = runtime.repoId ?? null
      } catch (error) {
        setLoadError(describeApiError(error))
      }

      const byId = new Map(scopes.map((scope) => [scope.id, scope]))
      // The server's landing repo may not be in the scope list (e.g. anonymous/admin wiring);
      // keep it selectable.
      if (runtimeRepoId && !byId.has(runtimeRepoId)) {
        byId.set(runtimeRepoId, { id: runtimeRepoId, name: runtimeRepoId, registered: false })
      }

      // Resolve the authoritative selection now that scopes are known:
      //   URL (trusted — deep link / shareable)  ->  a still-valid stored repo  ->  runtime
      //   default  ->  first available project.
      // Validating `stored` against the scope set is what prevents a deleted/renamed project from
      // being queried forever.
      let sorted = Array.from(byId.values()).sort((a, b) => a.name.localeCompare(b.name))
      const resolved =
        fromUrl ||
        (stored && byId.has(stored) ? stored : null) ||
        runtimeRepoId ||
        sorted[0]?.id ||
        null

      // Guarantee the resolved repo is a selectable <option> so the dropdown never shows a value
      // with no matching option.
      if (resolved && !byId.has(resolved)) {
        byId.set(resolved, { id: resolved, name: resolved, registered: false })
        sorted = Array.from(byId.values()).sort((a, b) => a.name.localeCompare(b.name))
      }
      setProjects(sorted)

      if (resolved && resolved !== selectedRef.current) {
        selectProject(resolved, true)
      }
      setReady(true)
    })()
  }, [selectProject])

  // Keep React state in sync with browser Back/Forward, which change the URL without a router
  // navigation. If the popped entry has no repo_id, re-reflect the current selection into the URL
  // rather than leaving the address bar and rendered project diverged.
  useEffect(() => {
    const onPopState = () => {
      const fromUrl = currentRepoIdFromUrl()
      if (fromUrl) {
        applySelection(fromUrl)
      } else if (selectedRef.current) {
        writeRepoIdToUrl(selectedRef.current)
      }
    }
    window.addEventListener("popstate", onPopState)
    return () => window.removeEventListener("popstate", onPopState)
  }, [applySelection])

  return (
    <SelectedProjectContext.Provider value={{ selectedRepoId, projects, loadError, selectProject }}>
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

// The dropdown's option list + any load error, owned by the provider so selection and the list
// share one source of truth.
export function useProjectScopes(): { projects: ProjectScope[]; loadError: string | null } {
  const ctx = useContext(SelectedProjectContext)
  return { projects: ctx?.projects ?? [], loadError: ctx?.loadError ?? null }
}

export function projectHref(path: string, repoId?: string | null): string {
  if (!repoId) return path

  const params = new URLSearchParams()
  params.set(PROJECT_QUERY_PARAM, repoId)
  return `${path}?${params.toString()}`
}
