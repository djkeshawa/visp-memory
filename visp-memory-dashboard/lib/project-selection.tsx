"use client"

import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react"
import { describeApiError, getProjectScopes, getRuntimeStatus } from "@/lib/api"
import type { ProjectScope } from "@/lib/types"

export const PROJECT_QUERY_PARAM = "repo_id"
export const PROJECT_STORAGE_KEY = "visp-memory-selected-repo-id"

// Upper bound on the initial scope/runtime load before the readiness gate is forced open with an
// error, so a hung (connected-but-silent) backend can't wedge the app on the spinner forever.
const LOAD_TIMEOUT_MS = 12000

interface SelectedProjectContextValue {
  selectedRepoId: string | null
  projects: ProjectScope[]
  loadError: string | null
  selectProject: (repoId: string, persist?: boolean) => void
  refreshProjectScopes: () => Promise<void>
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

function clearStoredRepoId() {
  try {
    window.localStorage.removeItem(PROJECT_STORAGE_KEY)
  } catch {
    /* persistence unavailable */
  }
}

// Update the address bar in place, with no navigation and no network request. This is the crux
// of the fix: in a Next static export served by a non-Next server, router.replace() for a
// query-only change triggers an RSC (.txt?_rsc=) fetch the static server can't answer, which the
// App Router recovers from with a full-page reload. history.replaceState mutates the URL without
// any of that, so the selection sticks. window.location.pathname already includes basePath.
function writeRepoIdToUrl(repoId: string | null) {
  const params = new URLSearchParams(window.location.search)
  if (repoId) params.set(PROJECT_QUERY_PARAM, repoId)
  else params.delete(PROJECT_QUERY_PARAM)
  const query = params.toString()
  const nextUrl = `${window.location.pathname}${query ? `?${query}` : ""}${window.location.hash}`
  window.history.replaceState(window.history.state, "", nextUrl)
}

export function SelectedProjectProvider({ children }: { children: ReactNode }) {
  // Start null on both server prerender and first client render so hydration matches; the real
  // value is resolved in the mount effect and gated by `ready`.
  const [selectedRepoId, setSelectedRepoId] = useState<string | null>(null)
  const [projects, setProjects] = useState<ProjectScope[]>([])
  const [loadError, setLoadError] = useState<string | null>(null)
  const [ready, setReady] = useState(false)
  const scopeRequestRef = useRef(0)
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

  const refreshProjectScopes = useCallback(async () => {
    const requestId = ++scopeRequestRef.current
    try {
      const scopes = await getProjectScopes()
      if (requestId !== scopeRequestRef.current) return

      const sorted = [...scopes].sort((left, right) => left.name.localeCompare(right.name))
      setProjects(sorted)
      setLoadError(null)

      const selected = selectedRef.current
      if (selected && sorted.some((scope) => scope.id === selected)) return

      const fallback = sorted[0]?.id ?? null
      applySelection(fallback)
      if (fallback) writeStoredRepoId(fallback)
      else clearStoredRepoId()
      writeRepoIdToUrl(fallback)
    } catch (error) {
      if (requestId !== scopeRequestRef.current) return
      // A failed refresh must not discard a selection or an option list that may still be valid.
      setLoadError(describeApiError(error))
    }
  }, [applySelection])

  // Cleanup cancels this effect's requests and timeout together. StrictMode can then restart
  // initialization with its own live requests and failsafe.
  useEffect(() => {
    let cancelled = false
    const controller = new AbortController()

    const fromUrl = currentRepoIdFromUrl()
    const stored = readStoredRepoId()

    // Fast path: if the URL or localStorage already names a repo, show the app immediately with
    // it so there is no loading flash on a warm load. persist=false: an unvalidated URL id must
    // not be written to localStorage until the async block validates it against the scope set. We
    // also seed the option list with the selected repo so the dropdown always has a matching,
    // enabled option during the load window (avoids a transient "No projects found").
    if (fromUrl || stored) {
      const seed = (fromUrl || stored) as string
      selectProject(seed, false)
      setProjects([{ id: seed, name: seed, registered: false }])
      setReady(true)
    }

    // Safety net: a stalled backend that accepts the socket but never responds would otherwise
    // leave a cold load wedged on the spinner forever (fetch has no timeout). Force the gate open
    // with an error after a bound.
    let settled = false
    const failsafe = setTimeout(() => {
      if (settled) return
      setLoadError((prev) => prev ?? "The server did not respond. Confirm the Visp Memory server is running.")
      setReady(true)
      controller.abort()
    }, LOAD_TIMEOUT_MS)

    const requestId = ++scopeRequestRef.current
    void (async () => {
      // allSettled, not Promise.all: a transient failure of one endpoint must not discard the
      // other's success (a runtime-status blip should not wipe out a loaded scope list).
      const [scopeResult, runtimeResult] = await Promise.allSettled([
        getProjectScopes(controller.signal), getRuntimeStatus(controller.signal),
      ])
      if (cancelled) return
      if (requestId !== scopeRequestRef.current) {
        // A mutation may have refreshed scopes while the warm-load request was still pending.
        // The refresh owns the state now, but this superseded request still owns the readiness
        // failsafe and must release it so it cannot surface a spurious timeout later.
        settled = true
        clearTimeout(failsafe)
        setReady(true)
        return
      }
      const scopes: ProjectScope[] = scopeResult.status === "fulfilled" ? scopeResult.value : []
      const runtimeRepoId: string | null =
        runtimeResult.status === "fulfilled" ? runtimeResult.value.repoId ?? null : null
      // Surface a load error ONLY when the scope list itself failed (the dropdown is then unusable).
      // A runtime-status-only blip is non-fatal — the dropdown still works from the scope list, and
      // the sidebar/system-status poll runtime separately. Always writing this (including the null
      // branch on success) also clears the failsafe timer's provisional "did not respond" message
      // when a slow-but-successful load eventually resolves.
      setLoadError(scopeResult.status === "rejected" ? describeApiError(scopeResult.reason) : null)

      settled = true
      clearTimeout(failsafe)

      const byId = new Map(scopes.map((scope) => [scope.id, scope]))
      // The server's landing repo may not be in the scope list (e.g. anonymous/admin wiring);
      // keep it selectable.
      if (runtimeRepoId && !byId.has(runtimeRepoId)) {
        byId.set(runtimeRepoId, { id: runtimeRepoId, name: runtimeRepoId, registered: false })
      }

      // If the user picked a project during the scope-load window, that deliberate choice is
      // authoritative — never override it with the value init resolved. `fastValue` is what the
      // fast path selected (or null on a cold boot); if the current selection has diverged from
      // it, the user chose.
      const fastValue = fromUrl || stored || null
      const userHasChosen = selectedRef.current !== fastValue

      // Otherwise finalize the init-resolved selection now that scopes are known:
      //   a still-valid URL repo  ->  a still-valid stored repo  ->  runtime default  ->  first
      //   available project. The URL and stored ids are validated against the scope set (the
      //   runtime default is injected above), so a deleted/renamed repo carried in a shared link
      //   or left in localStorage falls back to a real project instead of pinning the app to an
      //   invalid scope. But ONLY validate when the scope list actually loaded — if
      //   getProjectScopes failed, we have no basis to reject and must trust the URL/stored value
      //   (rejecting a valid deep link on a transient scopes blip would be worse).
      const scopesLoaded = scopeResult.status === "fulfilled"
      const isSelectable = (id: string) => !scopesLoaded || byId.has(id)
      const sorted = () => Array.from(byId.values()).sort((a, b) => a.name.localeCompare(b.name))
      const finalId = userHasChosen
        ? selectedRef.current
        : (fromUrl && isSelectable(fromUrl) ? fromUrl : null) ||
          (stored && isSelectable(stored) ? stored : null) ||
          runtimeRepoId ||
          sorted()[0]?.id ||
          null

      // Guarantee the active selection is a selectable <option> so the dropdown never shows a
      // value with no matching option (covers a user-chosen out-of-scope repo).
      if (finalId && !byId.has(finalId)) {
        byId.set(finalId, { id: finalId, name: finalId, registered: false })
      }
      setProjects(sorted())

      // Persist the validated final selection (the fast path deliberately did not persist an
      // unvalidated URL id). Re-running selectProject when finalId is unchanged is a harmless
      // no-op on state/URL and simply commits the now-validated value to localStorage.
      if (finalId) {
        selectProject(finalId, true)
      }
      setReady(true)
    })()

    return () => {
      cancelled = true
      clearTimeout(failsafe)
      controller.abort()
    }
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
    <SelectedProjectContext.Provider
      value={{ selectedRepoId, projects, loadError, selectProject, refreshProjectScopes }}
    >
      {ready ? children : <ProjectLoadingShell />}
    </SelectedProjectContext.Provider>
  )
}

function ProjectLoadingShell() {
  return (
    <div
      role="status"
      aria-live="polite"
      aria-busy="true"
      className="flex min-h-screen items-center justify-center bg-background"
    >
      <div
        aria-hidden="true"
        className="h-8 w-8 animate-spin rounded-full border-2 border-muted border-t-foreground"
      />
      <span className="sr-only">Loading projects…</span>
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

export function useRefreshProjectScopes(): () => Promise<void> {
  const ctx = useContext(SelectedProjectContext)
  if (!ctx) {
    throw new Error("useRefreshProjectScopes must be used within a SelectedProjectProvider")
  }
  return ctx.refreshProjectScopes
}

export function projectHref(path: string, repoId?: string | null): string {
  if (!repoId) return path

  const params = new URLSearchParams()
  params.set(PROJECT_QUERY_PARAM, repoId)
  return `${path}?${params.toString()}`
}
