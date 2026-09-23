"use client"

import { FormEvent, useEffect, useState } from "react"
import { Plus, RefreshCw, ShieldCheck, UserRound } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { createAccount, describeApiError, listAccounts, setAccountEnabled } from "@/lib/api"
import type { AuthUser } from "@/lib/types"

export default function UsersPage() {
  const [users, setUsers] = useState<AuthUser[]>([])
  const [username, setUsername] = useState("")
  const [displayName, setDisplayName] = useState("")
  const [password, setPassword] = useState("")
  const [role, setRole] = useState<"admin" | "user">("user")
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
    <div className="space-y-7">
      <header className="flex items-end justify-between border-b border-border pb-5">
        <div>
          <h1 className="text-2xl font-semibold">Users</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Manage dashboard identities and administrator access.
          </p>
        </div>
        <Button size="sm" variant="outline" onClick={() => void load()} disabled={loading}>
          <RefreshCw className={loading ? "h-4 w-4 animate-spin" : "h-4 w-4"} />
          <span>Refresh</span>
        </Button>
      </header>

      {error ? (
        <p className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive" role="alert">
          {error}
        </p>
      ) : null}

      <section className="border-b border-border pb-7">
        <div className="flex items-center gap-2">
          <Plus className="h-5 w-5 text-highlight" />
          <h2 className="text-lg font-semibold">Create account</h2>
        </div>
        <form onSubmit={create} className="mt-4 grid gap-4 md:grid-cols-[1fr_1fr_1fr_0.7fr_auto] md:items-end">
          <div className="space-y-2">
            <Label htmlFor="new-username">Username</Label>
            <Input id="new-username" value={username} onChange={(event) => setUsername(event.target.value)} minLength={3} required />
          </div>
          <div className="space-y-2">
            <Label htmlFor="new-display-name">Display name</Label>
            <Input id="new-display-name" value={displayName} onChange={(event) => setDisplayName(event.target.value)} />
          </div>
          <div className="space-y-2">
            <Label htmlFor="new-password">Temporary password</Label>
            <Input id="new-password" type="password" value={password} onChange={(event) => setPassword(event.target.value)} minLength={12} required />
          </div>
          <label className="space-y-2 text-sm font-medium">
            Role
            <select value={role} onChange={(event) => setRole(event.target.value as "admin" | "user")} className="mt-2 h-10 w-full rounded-md border border-input bg-background px-3">
              <option value="user">User</option>
              <option value="admin">Admin</option>
            </select>
          </label>
          <Button type="submit"><Plus className="h-4 w-4" /><span>Create</span></Button>
        </form>
      </section>

      <section>
        <h2 className="text-lg font-semibold">Accounts</h2>
        <div className="mt-3 overflow-x-auto rounded-lg border border-border">
          <table className="w-full min-w-[40rem] text-left text-sm">
            <thead className="bg-secondary/60 text-xs text-muted-foreground">
              <tr><th className="px-4 py-3">User</th><th className="px-4 py-3">Role</th><th className="px-4 py-3">Last login</th><th className="px-4 py-3 text-right">Access</th></tr>
            </thead>
            <tbody className="divide-y divide-border">
              {users.map((user) => (
                <tr key={user.id}>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-3">
                      <span className="flex h-8 w-8 items-center justify-center rounded-md bg-secondary">
                        {user.role === "admin" ? <ShieldCheck className="h-4 w-4 text-highlight" /> : <UserRound className="h-4 w-4" />}
                      </span>
                      <div><p className="font-medium">{user.displayName || user.username}</p><p className="text-xs text-muted-foreground">@{user.username}</p></div>
                    </div>
                  </td>
                  <td className="px-4 py-3 capitalize">{user.role}</td>
                  <td className="px-4 py-3 text-muted-foreground">{user.lastLoginAt ? new Date(user.lastLoginAt).toLocaleString() : "Never"}</td>
                  <td className="px-4 py-3 text-right"><Button size="sm" variant="outline" onClick={() => void toggleEnabled(user)}>{user.enabled ? "Disable" : "Enable"}</Button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  )
}
