"use client"

import { useEffect, useMemo, useState } from "react"
import { Button } from "@/components/ui/button"
import { PageHeader } from "@/components/strata/primitives"
import {
  createPersonalAccessToken,
  describeApiError,
  getProjectScopes,
  listPersonalAccessTokens,
  revokePersonalAccessToken,
} from "@/lib/api"
import type { PersonalAccessToken, ProjectScope } from "@/lib/types"
import { AVAILABLE_SCOPES, CreateTokenForm } from "./_components/create-token-form"
import { TokenCreated } from "./_components/token-created"
import { TokensTable } from "./_components/tokens-table"

export default function IntegrationsPage() {
  const [tokens, setTokens] = useState<PersonalAccessToken[]>([])
  const [projects, setProjects] = useState<ProjectScope[]>([])
  const [name, setName] = useState("")
  const [scopes, setScopes] = useState<string[]>(AVAILABLE_SCOPES.map(([scope]) => scope))
  const [repoIds, setRepoIds] = useState<string[]>([])
  const [createdSecret, setCreatedSecret] = useState<string | null>(null)
  const [createdName, setCreatedName] = useState<string | null>(null)
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
      setCreatedName(token.name)
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
    try {
      await navigator.clipboard.writeText(createdSecret)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1800)
    } catch {
      setError("Copying was blocked by the browser. Select the token text and copy it before closing this panel.")
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        eyebrow="Scoped credentials for API and MCP clients"
        title="Integrations"
        actions={<Button variant="outline" className="h-10" onClick={() => void load()} disabled={loading}>Refresh</Button>}
      />

      {createdSecret ? (
        <TokenCreated name={createdName} secret={createdSecret} copied={copied} onCopy={() => void copySecret()} onDone={() => setCreatedSecret(null)} />
      ) : null}

      {error ? <p className="rounded-xl border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive" role="alert">{error}</p> : null}

      <div className="flex flex-wrap items-start gap-6">
        <CreateTokenForm
          name={name}
          scopes={scopes}
          repoIds={repoIds}
          projects={projects}
          saving={saving}
          onName={setName}
          onToggleScope={(scope) => toggle(scope, scopes, setScopes)}
          onToggleProject={(id) => toggle(id, repoIds, setRepoIds)}
          onSubmit={() => void createToken()}
        />
        <TokensTable tokens={activeTokens} projects={projects} loading={loading} onRevoke={(id) => void revoke(id)} />
      </div>
    </div>
  )
}
