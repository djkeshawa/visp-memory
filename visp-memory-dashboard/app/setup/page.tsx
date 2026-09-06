"use client"

import Link from "next/link"
import { Suspense, useEffect, useState } from "react"
import { CheckCircle2, RefreshCw } from "lucide-react"
import { Button } from "@/components/ui/button"
import { dashboardReturnPath } from "@/lib/auth-navigation"
import { describeApiError, getEmbeddingIndexStatus, getRuntimeStatus } from "@/lib/api"
import { useSelectedProjectId } from "@/lib/project-selection"
import type { EmbeddingIndexStatus, RuntimeStatus } from "@/lib/types"

const choices = {
  keyword: { title: "Keyword search", detail: "Works immediately without a model or API key.", config: "VISP_MEMORY_EMBEDDING_PROVIDER=none" },
  openrouter: { title: "OpenRouter", detail: "Use your server's OpenRouter key to generate embeddings.", config: "VISP_MEMORY_EMBEDDING_PROVIDER=openrouter\nEMBEDDING_API_KEY=<your-provider-key>" },
  openai: { title: "OpenAI", detail: "Use your server's OpenAI key to generate embeddings.", config: "VISP_MEMORY_EMBEDDING_PROVIDER=openai\nEMBEDDING_API_KEY=<your-provider-key>" },
  ollama: { title: "Local Ollama", detail: "Generate embeddings locally with a downloaded model.", config: "VISP_MEMORY_EMBEDDING_PROVIDER=ollama\nVISP_MEMORY_EMBEDDING_MODEL=nomic-embed-text\nOLLAMA_HOST=http://ollama:11434" },
}

function SetupContent() {
  const repoId = useSelectedProjectId()
  const [choice, setChoice] = useState<keyof typeof choices>("keyword")
  const [runtime, setRuntime] = useState<RuntimeStatus | null>(null)
  const [index, setIndex] = useState<EmbeddingIndexStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    let active = true
    setLoading(true)
    setRuntime(null)
    setIndex(null)
    Promise.all([getRuntimeStatus(), getEmbeddingIndexStatus(repoId)])
      .then(([server, embedding]) => { if (active) { setRuntime(server); setIndex(embedding); setError(null) } })
      .catch((failure) => { if (active) setError(describeApiError(failure)) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [repoId, attempt])
  const semantic = runtime?.embeddingDriverConnected && index?.status === "available"
  return (
    <div className="mx-auto max-w-2xl space-y-7">
      <header>
        <p className="text-sm font-medium text-primary">Workspace setup</p>
        <h1 className="mt-2 text-3xl font-semibold">Choose how to find your memories</h1>
        <p className="mt-3 text-sm leading-6 text-muted-foreground">Start with keyword search, or connect a model for meaning-based matches. You can revisit this guide from Settings.</p>
      </header>
      <section className="rounded-xl border border-border bg-card p-5" aria-label="Current search setup">
        <div className="flex items-center justify-between gap-4">
          <div>
            <h2 className="font-semibold">Current server configuration</h2>
            <p role="status" className="mt-2 text-sm text-muted-foreground">{loading ? "Checking search setup…" : error ? "Search setup could not be checked" : semantic ? "Semantic search is available" : "Keyword search is available"}</p>
          </div>
          <Button variant="outline" size="sm" disabled={loading} onClick={() => setAttempt((value) => value + 1)}><RefreshCw className="h-4 w-4" />Check again</Button>
        </div>
        {error && <p role="alert" className="mt-3 text-sm text-destructive">{error}</p>}
        {index && <p className="mt-3 text-xs leading-5 text-muted-foreground">{index.message}</p>}
        {runtime?.embeddingDriverConnected && index?.status !== "available" && <p className="mt-3 text-sm">Your provider is connected, but this store still needs a vector index before it can search by meaning.</p>}
      </section>
      <fieldset>
        <legend className="mb-3 font-semibold">Explore search options</legend>
        <div className="grid gap-3 sm:grid-cols-2">
          {Object.entries(choices).map(([key, item]) => <label key={key} className={`cursor-pointer rounded-xl border p-4 ${choice === key ? "border-primary bg-primary/5" : "border-border"}`}>
            <span className="flex items-center gap-2 text-sm font-medium"><input type="radio" name="search-choice" checked={choice === key} onChange={() => setChoice(key as keyof typeof choices)} />{item.title}</span>
            <span className="mt-2 block text-xs leading-5 text-muted-foreground">{item.detail}</span>
          </label>)}
        </div>
      </fieldset>
      {choice === "keyword" ? <p className="flex gap-2 text-sm text-muted-foreground"><CheckCircle2 className="h-5 w-5 shrink-0" />Keyword search needs no setup. Exploring these options does not change your server configuration.</p> : <section className="space-y-3 rounded-xl border border-border p-5">
        <h2 className="font-semibold">Configure {choices[choice].title}</h2>
        <p className="text-sm leading-6 text-muted-foreground">Add these settings to your server environment, then restart it. Keep provider keys on the server. This page does not save or expose credentials.</p>
        <pre className="overflow-x-auto rounded-lg bg-secondary p-3 text-xs">{choices[choice].config}</pre>
        {choice === "ollama" && <p className="text-sm text-muted-foreground">With the project Compose files, use docker-compose.ollama.yml to download the selected model and start Ollama before the app.</p>}
        <p className="text-sm text-muted-foreground">SQLite also requires the optional Chroma vector index. After configuration, use Settings to inspect the index and rebuild embeddings for existing memories.</p>
        <Link className="inline-block text-sm font-medium text-primary underline" href="/settings">Open embedding diagnostics</Link>
      </section>}
      <div className="flex flex-wrap items-center justify-between gap-4 border-t border-border pt-5">
        <Link href="/projects" className="text-sm text-primary underline">Add or select a project</Link>
        <Button onClick={() => window.location.replace(dashboardReturnPath())}>Continue to workspace</Button>
      </div>
    </div>
  )
}

export default function SetupPage() {
  return <Suspense fallback={null}><SetupContent /></Suspense>
}
