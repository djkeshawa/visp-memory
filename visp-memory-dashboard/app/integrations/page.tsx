"use client"

import { useEffect, useMemo, useState } from "react"
import { Check, Copy, KeyRound, Plus, RefreshCw, Shield, Trash2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  createPersonalAccessToken,
  describeApiError,
  getProjectScopes,
  listPersonalAccessTokens,
  revokePersonalAccessToken,
} from "@/lib/api"
import type { PersonalAccessToken, ProjectScope } from "@/lib/types"

const availableScopes = [
  ["memory:read", "Read memories"],
  ["memory:write", "Create and update memories"],
  ["intent:read", "Read intents"],
  ["intent:write", "Create and complete intents"],
  ["project:read", "Read project metadata"],
] as const

export default function IntegrationsPage() {
  const [tokens, setTokens] = useState<PersonalAccessToken[]>([])
  const [projects, setProjects] = useState<ProjectScope[]>([])
  const [name, setName] = useState("")
  const [scopes, setScopes] = useState<string[]>(availableScopes.map(([scope]) => scope))
  const [repoIds, setRepoIds] = useState<string[]>([])
  const [createdSecret, setCreatedSecret] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const activeTokens = useMemo(() => tokens.filter((token) => !token.revokedAt), [tokens])

  const load = async () => {
    setLoading(true)
    try {
      const [tokenData, projectData] = await Promise.all([
        listPersonalAccessTokens(),
        getProjectScopes(),
      ])
      setTokens(tokenData)
      setProjects(projectData)
      setError(null)
    } catch (loadError) {
      setError(describeApiError(loadError))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { void load() }, [])

  const toggle = (value: string, current: string[], update: (next: string[]) => void) => {
    update(current.includes(value) ? current.filter((item) => item !== value) : [...current, value])
  }

  const createToken = async () => {
    if (!name.trim() || scopes.length === 0) {
      setError("Give the token a name and select at least one permission.")
      return
    }
    setSaving(true)
    try {
      const token = await createPersonalAccessToken({ name, scopes, repoIds })
      setTokens((current) => [token, ...current])
      setCreatedSecret(token.token || null)
      setName("")
      setError(null)
    } catch (saveError) {
      setError(describeApiError(saveError))
    } finally {
      setSaving(false)
    }
  }

  const revoke = async (tokenId: string) => {
    try {
      await revokePersonalAccessToken(tokenId)
      await load()
    } catch (revokeError) {
      setError(describeApiError(revokeError))
    }
  }

  const copySecret = async () => {
    if (!createdSecret) return
    await navigator.clipboard.writeText(createdSecret)
    setCopied(true)
    window.setTimeout(() => setCopied(false), 1800)
  }

  return (
    <div className="space-y-7">
      <header className="flex flex-col gap-3 border-b border-border pb-5 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-foreground">Integrations</h1>
          <p className="mt-1 text-sm text-muted-foreground">Issue scoped credentials for API and MCP clients.</p>
        </div>
        <Button variant="outline" size="sm" onClick={() => void load()} disabled={loading}>
          <RefreshCw className={loading ? "h-4 w-4 animate-spin" : "h-4 w-4"} />
          <span>Refresh</span>
        </Button>
      </header>

      {createdSecret ? (
        <section className="rounded-lg border border-success/35 bg-success/5 p-4" aria-labelledby="new-token-heading">
          <div className="flex items-start gap-3">
            <Shield className="mt-0.5 h-5 w-5 text-success" />
            <div className="min-w-0 flex-1">
              <h2 id="new-token-heading" className="font-semibold text-foreground">Token created</h2>
              <p className="mt-1 text-sm text-muted-foreground">This secret is shown once. Add it to your API or MCP client now.</p>
              <div className="mt-3 flex items-center gap-2">
                <code className="min-w-0 flex-1 overflow-x-auto rounded-md border border-border bg-background px-3 py-2 text-xs">{createdSecret}</code>
                <Button size="sm" variant="outline" onClick={copySecret} aria-label="Copy token">
                  {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
                </Button>
              </div>
            </div>
          </div>
        </section>
      ) : null}

      {error ? <p className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive" role="alert">{error}</p> : null}

      <section className="grid gap-6 border-b border-border pb-7 lg:grid-cols-[minmax(0,1fr)_minmax(18rem,0.7fr)]">
        <div>
          <div className="flex items-center gap-2">
            <KeyRound className="h-5 w-5 text-primary" />
            <h2 className="text-lg font-semibold">Create access token</h2>
          </div>
          <div className="mt-4 space-y-5">
            <div className="space-y-2">
              <Label htmlFor="token-name">Token name</Label>
              <Input id="token-name" value={name} onChange={(event) => setName(event.target.value)} placeholder="Codex workspace" />
            </div>
            <fieldset>
              <legend className="text-sm font-medium">Permissions</legend>
              <div className="mt-2 grid gap-2 sm:grid-cols-2">
                {availableScopes.map(([scope, label]) => (
                  <label key={scope} className="flex items-start gap-2 rounded-md border border-border px-3 py-2 text-sm">
                    <input type="checkbox" className="mt-0.5" checked={scopes.includes(scope)} onChange={() => toggle(scope, scopes, setScopes)} />
                    <span><span className="block text-foreground">{label}</span><code className="text-xs text-muted-foreground">{scope}</code></span>
                  </label>
                ))}
              </div>
            </fieldset>
          </div>
        </div>
        <div>
          <h2 className="text-sm font-medium">Project access</h2>
          <p className="mt-1 text-xs text-muted-foreground">No selection means every project currently available to your account.</p>
          <div className="mt-3 max-h-64 space-y-2 overflow-y-auto">
            {projects.map((project) => (
              <label key={project.id} className="flex items-center gap-2 rounded-md border border-border px-3 py-2 text-sm">
                <input type="checkbox" checked={repoIds.includes(project.id)} onChange={() => toggle(project.id, repoIds, setRepoIds)} />
                <span className="min-w-0 truncate">{project.name}</span>
              </label>
            ))}
          </div>
          <Button className="mt-5 w-full" onClick={createToken} disabled={saving}>
            <Plus className="h-4 w-4" />
            <span>{saving ? "Creating..." : "Create token"}</span>
          </Button>
        </div>
      </section>

      <section className="min-w-0 overflow-hidden">
        <h2 className="text-lg font-semibold">Active tokens</h2>
        <div className="mt-3 w-full max-w-full overflow-x-auto rounded-lg border border-border">
          <table className="hidden w-full min-w-[42rem] text-left text-sm sm:table">
            <thead className="bg-secondary/60 text-xs text-muted-foreground"><tr><th className="px-4 py-3">Name</th><th className="px-4 py-3">Prefix</th><th className="px-4 py-3">Permissions</th><th className="px-4 py-3">Last used</th><th className="w-14 px-4 py-3"><span className="sr-only">Actions</span></th></tr></thead>
            <tbody className="divide-y divide-border">
              {activeTokens.map((token) => (
                <tr key={token.id}><td className="px-4 py-3 font-medium">{token.name}</td><td className="px-4 py-3"><code>{token.tokenPrefix}</code></td><td className="max-w-xs px-4 py-3 text-xs text-muted-foreground">{token.scopes.join(", ")}</td><td className="px-4 py-3 text-muted-foreground">{token.lastUsedAt ? new Date(token.lastUsedAt).toLocaleString() : "Never"}</td><td className="px-4 py-3"><Button size="sm" variant="ghost" onClick={() => void revoke(token.id)} aria-label={`Revoke ${token.name}`}><Trash2 className="h-4 w-4 text-destructive" /></Button></td></tr>
              ))}
              {!loading && activeTokens.length === 0 ? <tr><td colSpan={5} className="px-4 py-8 text-center text-muted-foreground">No active integration tokens.</td></tr> : null}
            </tbody>
          </table>
          <div className="divide-y divide-border sm:hidden">
            {activeTokens.map((token) => (
              <div key={token.id} className="space-y-3 p-4 text-sm">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="truncate font-medium text-foreground">{token.name}</p>
                    <code className="text-xs text-muted-foreground">{token.tokenPrefix}</code>
                  </div>
                  <Button size="sm" variant="ghost" onClick={() => void revoke(token.id)} aria-label={`Revoke ${token.name}`}>
                    <Trash2 className="h-4 w-4 text-destructive" />
                  </Button>
                </div>
                <p className="break-words text-xs text-muted-foreground">{token.scopes.join(", ")}</p>
                <p className="text-xs text-muted-foreground">
                  Last used: {token.lastUsedAt ? new Date(token.lastUsedAt).toLocaleString() : "Never"}
                </p>
              </div>
            ))}
            {!loading && activeTokens.length === 0 ? (
              <p className="px-4 py-8 text-center text-sm text-muted-foreground">No active integration tokens.</p>
            ) : null}
          </div>
        </div>
      </section>
    </div>
  )
}
