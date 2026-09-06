"use client"

import { FormEvent, useEffect, useState } from "react"
import { KeyRound, LockKeyhole, LogIn, ShieldCheck, UserRound } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { dashboardReturnPath } from "@/lib/auth-navigation"
import {
  describeApiError,
  getAuthenticationStatus,
  getCurrentAccount,
  login,
} from "@/lib/api"

export default function AuthenticationPage() {
  const [username, setUsername] = useState("")
  const [password, setPassword] = useState("")
  const [message, setMessage] = useState<string | null>(null)
  const [setupRequired, setSetupRequired] = useState(false)
  const [authDisabled, setAuthDisabled] = useState(false)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [isInitializing, setIsInitializing] = useState(true)

  useEffect(() => {
    const initialize = async () => {
      try {
        const status = await getAuthenticationStatus()
        setSetupRequired(status.setupRequired)
        if (!status.authEnabled) {
          setAuthDisabled(true)
          setMessage("Authentication is disabled for this server. You can open the dashboard directly.")
          return
        }
        try {
          await getCurrentAccount()
          window.location.replace(dashboardReturnPath())
        } catch {
          // The visitor is not signed in yet.
        }
      } catch (error) {
        setMessage(describeApiError(error))
      } finally {
        setIsInitializing(false)
      }
    }
    void initialize()
  }, [])

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault()
    if (authDisabled) {
      window.location.replace(dashboardReturnPath())
      return
    }
    if (!username.trim() || !password) {
      setMessage("Enter your username and password.")
      return
    }
    setIsSubmitting(true)
    setMessage(null)
    try {
      await login(username, password)
      window.location.replace(dashboardReturnPath())
    } catch (error) {
      setMessage(describeApiError(error))
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <div className="mx-auto flex min-h-[calc(100vh-8rem)] max-w-md items-center py-8">
      <section className="w-full overflow-hidden rounded-lg border border-border bg-card shadow-sm" aria-labelledby="login-heading">
        <div className="border-b border-border bg-secondary/45 px-6 py-5">
          <div className="flex items-center gap-3">
            <div className="flex h-11 w-11 items-center justify-center rounded-md bg-primary text-primary-foreground">
              <ShieldCheck className="h-5 w-5" aria-hidden="true" />
            </div>
            <div>
              <h1 id="login-heading" className="text-xl font-semibold text-foreground">Sign in</h1>
              <p className="mt-1 text-sm text-muted-foreground">Access your Visp Memory workspace</p>
            </div>
          </div>
        </div>

        <form className="space-y-5 px-6 py-6" onSubmit={handleSubmit}>
          <div className="space-y-2">
            <Label htmlFor="username">Username</Label>
            <div className="relative">
              <UserRound className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
              <Input
                id="username"
                autoComplete="username"
                className="pl-9"
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                disabled={authDisabled || isInitializing}
                autoFocus
              />
            </div>
          </div>
          <div className="space-y-2">
            <Label htmlFor="password">Password</Label>
            <div className="relative">
              <LockKeyhole className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
              <Input
                id="password"
                type="password"
                autoComplete="current-password"
                className="pl-9"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                disabled={authDisabled || isInitializing}
              />
            </div>
          </div>

          {setupRequired ? (
            <div className="flex gap-3 rounded-md border border-intent/35 bg-intent/5 p-3 text-sm text-muted-foreground">
              <KeyRound className="mt-0.5 h-4 w-4 shrink-0 text-intent" />
              <p>No administrator account exists yet. Create one with the server admin command, then sign in here.</p>
            </div>
          ) : null}

          {message ? <p className="text-sm text-destructive" role="alert">{message}</p> : null}

          <Button type="submit" className="w-full" disabled={isInitializing || isSubmitting || setupRequired}>
            <LogIn className="h-4 w-4" aria-hidden="true" />
            <span>{isInitializing ? "Checking session…" : authDisabled ? "Open dashboard" : isSubmitting ? "Signing in..." : "Sign in"}</span>
          </Button>
        </form>
      </section>
    </div>
  )
}
