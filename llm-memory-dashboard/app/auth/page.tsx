"use client"

import { useEffect, useState } from "react"
import Link from "next/link"
import { KeyRound, LogIn, LogOut, ShieldCheck } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  clearAuthCredentials,
  describeApiError,
  getAuthCredentials,
  getRuntimeStatus,
  setAuthCredentials,
} from "@/lib/api"

export default function AuthenticationPage() {
  const [apiKey, setApiKey] = useState("")
  const [jwtToken, setJwtToken] = useState("")
  const [message, setMessage] = useState<string | null>(null)
  const [isError, setIsError] = useState(false)
  const [isSubmitting, setIsSubmitting] = useState(false)

  useEffect(() => {
    const credentials = getAuthCredentials()
    setApiKey(credentials.apiKey)
    setJwtToken(credentials.jwtToken)
  }, [])

  const handleAuthenticate = async () => {
    if (!apiKey.trim() && !jwtToken.trim()) {
      setIsError(true)
      setMessage("Enter an API key or JWT token.")
      return
    }

    setIsSubmitting(true)
    setAuthCredentials(apiKey, jwtToken)
    try {
      await getRuntimeStatus()
      setIsError(false)
      setMessage("Authenticated. Opening the dashboard...")
      window.location.assign("/dashboard")
    } catch (error) {
      clearAuthCredentials()
      setIsError(true)
      setMessage(describeApiError(error))
    } finally {
      setIsSubmitting(false)
    }
  }

  const handleClear = () => {
    clearAuthCredentials()
    setApiKey("")
    setJwtToken("")
    setIsError(false)
    setMessage("Credentials cleared from this browser session.")
  }

  return (
    <div className="mx-auto flex min-h-[calc(100vh-8rem)] max-w-lg items-center py-8">
      <section className="w-full rounded-lg border border-border bg-card p-6" aria-labelledby="auth-heading">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-md bg-primary text-primary-foreground">
            <ShieldCheck className="h-5 w-5" aria-hidden="true" />
          </div>
          <div>
            <h1 id="auth-heading" className="text-xl font-semibold text-foreground">
              Authentication
            </h1>
            <p className="text-sm text-muted-foreground">Connect this browser session to LLM Memory.</p>
          </div>
        </div>

        <div className="mt-6 space-y-4">
          <div className="space-y-2">
            <Label htmlFor="auth-api-key">API key</Label>
            <Input
              id="auth-api-key"
              type="password"
              autoComplete="off"
              value={apiKey}
              onChange={(event) => setApiKey(event.target.value)}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="auth-jwt-token">JWT token</Label>
            <Input
              id="auth-jwt-token"
              type="password"
              autoComplete="off"
              value={jwtToken}
              onChange={(event) => setJwtToken(event.target.value)}
            />
          </div>
        </div>

        <p className="mt-3 text-xs text-muted-foreground">
          Credentials remain in session storage and are cleared when the browser session ends.
        </p>

        <div className="mt-5 flex flex-wrap items-center gap-2">
          <Button onClick={handleAuthenticate} disabled={isSubmitting}>
            <LogIn className="h-4 w-4" aria-hidden="true" />
            <span>{isSubmitting ? "Checking..." : "Authenticate"}</span>
          </Button>
          <Button variant="outline" onClick={handleClear} disabled={isSubmitting}>
            <LogOut className="h-4 w-4" aria-hidden="true" />
            <span>Clear</span>
          </Button>
          <Button variant="ghost" asChild>
            <Link href="/settings">
              <KeyRound className="h-4 w-4" aria-hidden="true" />
              <span>Advanced settings</span>
            </Link>
          </Button>
        </div>

        {message ? (
          <p
            className={isError ? "mt-4 text-sm text-destructive" : "mt-4 text-sm text-muted-foreground"}
            role={isError ? "alert" : "status"}
          >
            {message}
          </p>
        ) : null}
      </section>
    </div>
  )
}
