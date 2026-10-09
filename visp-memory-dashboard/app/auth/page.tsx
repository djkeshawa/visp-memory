"use client"

import { FormEvent, useEffect, useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { dashboardReturnPath } from "@/lib/auth-navigation"
import {
  createInitialAccount,
  describeApiError,
  getAuthenticationStatus,
  getCurrentAccount,
  login,
} from "@/lib/api"
import { BrandPanel } from "./_components/brand-panel"

const FIELD = "h-12 rounded-[10px] px-3.5 text-[15px] md:text-[15px]"

export default function AuthenticationPage() {
  const [username, setUsername] = useState("")
  const [password, setPassword] = useState("")
  const [confirmation, setConfirmation] = useState("")
  const [setupToken, setSetupToken] = useState("")
  const [message, setMessage] = useState<string | null>(null)
  const [setupRequired, setSetupRequired] = useState(false)
  const [authDisabled, setAuthDisabled] = useState(false)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [isInitializing, setIsInitializing] = useState(true)

  useEffect(() => {
    const token = new URLSearchParams(window.location.hash.slice(1)).get("setup")
    if (token) {
      setSetupToken(token)
      window.history.replaceState(window.history.state, "", window.location.pathname + window.location.search)
    }
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
    if (setupRequired && password !== confirmation) {
      setMessage("The passwords do not match.")
      return
    }
    setIsSubmitting(true)
    setMessage(null)
    try {
      const firstSetup = setupRequired
      if (firstSetup) {
        await createInitialAccount(username, password, setupToken)
        setSetupRequired(false)
      }
      await login(username, password)
      window.location.replace(firstSetup
        ? `/dashboard/setup?${new URLSearchParams({ next: dashboardReturnPath() })}`
        : dashboardReturnPath())
    } catch (error) {
      setMessage(describeApiError(error))
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <div className="-m-4 flex min-h-screen flex-wrap bg-background md:-m-8">
      <BrandPanel />
      <section aria-labelledby="login-heading" className="flex flex-[1_1_420px] items-center justify-center px-4 py-10 sm:py-16">
        <form className="flex w-full max-w-[380px] flex-col gap-[18px]" onSubmit={handleSubmit}>
          <div className="flex flex-col gap-1.5">
            <h1 id="login-heading" className="text-[28px] font-semibold">{setupRequired ? "Create administrator" : "Sign in"}</h1>
            <p className="text-muted-foreground">{setupRequired ? "Set up your account, then choose how to search." : "Access your visp memory workspace."}</p>
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="username" className="text-[13px] font-semibold">Username</Label>
            <Input id="username" autoComplete="username" className={FIELD} value={username}
              onChange={(event) => setUsername(event.target.value)} disabled={authDisabled || isInitializing} autoFocus />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="password" className="text-[13px] font-semibold">Password</Label>
            <Input id="password" type="password" autoComplete={setupRequired ? "new-password" : "current-password"}
              minLength={setupRequired ? 12 : undefined} required className={FIELD} value={password}
              onChange={(event) => setPassword(event.target.value)} disabled={authDisabled || isInitializing} />
          </div>

          {setupRequired ? (
            <>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="confirm-password" className="text-[13px] font-semibold">Confirm password</Label>
                <Input id="confirm-password" type="password" autoComplete="new-password" className={FIELD}
                  required value={confirmation} onChange={(event) => setConfirmation(event.target.value)} />
                <p className="text-xs text-muted-foreground">Use at least 12 characters.</p>
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="setup-code" className="text-[13px] font-semibold">Setup code</Label>
                <Input id="setup-code" type="password" autoComplete="off" className={FIELD} required
                  value={setupToken} onChange={(event) => setSetupToken(event.target.value)} />
                <p className="text-xs leading-5 text-muted-foreground">
                  Open the setup link shown in your server&apos;s startup logs to fill this code automatically.
                  It can only create the first account.
                </p>
              </div>
            </>
          ) : null}

          {message ? <p className="text-sm text-destructive" role="alert">{message}</p> : null}

          <Button type="submit" className="h-12 w-full rounded-[10px] text-[15px] font-semibold" disabled={isInitializing || isSubmitting}>
            {isInitializing ? "Checking session…" : authDisabled ? "Open dashboard" : isSubmitting ? "Please wait…" : setupRequired ? "Create account and continue" : "Sign in"}
          </Button>
        </form>
      </section>
    </div>
  )
}
