"use client"

import { useEffect, useState, type ReactNode } from "react"
import { LockKeyhole } from "lucide-react"
import {
  AUTH_CHANGED_EVENT,
  AUTH_REQUIRED_EVENT,
  describeApiError,
  getAuthenticationStatus,
  getCurrentAccount,
  isApiError,
} from "@/lib/api"
import { redirectToLogin } from "@/lib/auth-navigation"

const SESSION_CHECK_TIMEOUT_MS = 12000

export function AuthGate({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<"checking" | "allowed" | "error">("checking")
  const [error, setError] = useState("")
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let generation = 0
    let controller: AbortController | undefined

    const requireLogin = () => {
      ++generation
      controller?.abort()
      setStatus("checking")
      redirectToLogin()
    }

    const checkSession = async () => {
      const requestId = ++generation
      controller?.abort()
      const requestController = new AbortController()
      controller = requestController
      const timeout = window.setTimeout(() => requestController.abort(), SESSION_CHECK_TIMEOUT_MS)
      setStatus("checking")
      try {
        const authentication = await getAuthenticationStatus(requestController.signal)
        if (authentication.authEnabled) await getCurrentAccount(requestController.signal)
        if (requestId === generation) setStatus("allowed")
      } catch (error) {
        if (requestId !== generation) return
        if (isApiError(error) && error.status === 401) {
          requireLogin()
        } else {
          setError(describeApiError(error))
          setStatus("error")
        }
      } finally {
        window.clearTimeout(timeout)
      }
    }

    void checkSession()
    window.addEventListener(AUTH_CHANGED_EVENT, checkSession)
    window.addEventListener(AUTH_REQUIRED_EVENT, requireLogin)
    return () => {
      ++generation
      controller?.abort()
      window.removeEventListener(AUTH_CHANGED_EVENT, checkSession)
      window.removeEventListener(AUTH_REQUIRED_EVENT, requireLogin)
    }
  }, [attempt])

  if (status === "allowed") return children

  return (
    <main className="flex min-h-screen items-center justify-center bg-background px-6">
      <div className="max-w-md text-center">
        <LockKeyhole className="mx-auto mb-4 h-7 w-7 text-muted-foreground" aria-hidden="true" />
        {status === "error" ? (
          <>
            <h1 className="text-xl font-semibold">Unable to check your session</h1>
            <p role="alert" className="mt-3 text-sm leading-6 text-muted-foreground">{error}</p>
            <button
              type="button"
              onClick={() => setAttempt((value) => value + 1)}
              className="mt-5 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground"
            >
              Try again
            </button>
          </>
        ) : (
          <p role="status" className="text-sm text-muted-foreground">Checking your session…</p>
        )}
      </div>
    </main>
  )
}
