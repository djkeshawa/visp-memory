"use client"

import { FormEvent, useEffect, useState } from "react"
import { Button } from "@/components/ui/button"
import { PageHeader } from "@/components/strata/primitives"
import { createAccount, describeApiError, listAccounts, setAccountEnabled } from "@/lib/api"
import type { AuthUser } from "@/lib/types"
import { AccountsTable } from "./_components/accounts-table"
import { CreateAccountForm, type AccountRole } from "./_components/create-account-form"

export default function UsersPage() {
  const [users, setUsers] = useState<AuthUser[]>([])
  const [username, setUsername] = useState("")
  const [displayName, setDisplayName] = useState("")
  const [password, setPassword] = useState("")
  const [role, setRole] = useState<AccountRole>("user")
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = async () => {
    setLoading(true)
    try {
      setUsers(await listAccounts())
      setError(null)
    } catch (loadError) {
      setError(describeApiError(loadError))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { void load() }, [])

  const create = async (event: FormEvent) => {
    event.preventDefault()
    try {
      await createAccount({ username, password, displayName, role })
      setUsername("")
      setDisplayName("")
      setPassword("")
      setRole("user")
      await load()
    } catch (saveError) {
      setError(describeApiError(saveError))
    }
  }

  const toggleEnabled = async (user: AuthUser) => {
    try {
      await setAccountEnabled(user.id, !user.enabled)
      await load()
    } catch (saveError) {
      setError(describeApiError(saveError))
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        eyebrow="Who can sign in to this server"
        title="Users"
        actions={<Button variant="outline" className="h-10" onClick={() => void load()} disabled={loading}>Refresh</Button>}
      />

      {error ? <p className="rounded-xl border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive" role="alert">{error}</p> : null}

      <div className="flex flex-wrap items-start gap-6">
        <AccountsTable users={users} onToggle={(user) => void toggleEnabled(user)} />
        <CreateAccountForm
          username={username}
          displayName={displayName}
          password={password}
          role={role}
          onUsername={setUsername}
          onDisplayName={setDisplayName}
          onPassword={setPassword}
          onRole={setRole}
          onSubmit={create}
        />
      </div>
    </div>
  )
}
