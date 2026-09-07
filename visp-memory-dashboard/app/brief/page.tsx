"use client"

import type { ReactNode } from "react"
import { useEffect, useMemo, useRef, useState } from "react"
import { motion } from "framer-motion"
import {
  AlertTriangle,
  Check,
  ClipboardCheck,
  Copy,
  FileCode2,
  RefreshCw,
  ShieldQuestion,
  Sparkles,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Slider } from "@/components/ui/slider"
import { Textarea } from "@/components/ui/textarea"
import { describeApiError, prepareTaskMemoryBrief } from "@/lib/api"
import { pageTransition } from "@/lib/animations"
import { useSelectedProjectId } from "@/lib/project-selection"
import type { TaskBriefSectionName, TaskMemoryBrief } from "@/lib/types"
import { cn } from "@/lib/utils"

const SECTION_LABELS: Record<TaskBriefSectionName, string> = {
  warnings: "Warnings",
  decisions: "Decisions",
  knowledge: "Knowledge",
  history: "History",
}

function splitLines(value: string): string[] {
  return value
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean)
}

export default function TaskBriefPage() {
  const selectedRepoId = useSelectedProjectId()
  const [task, setTask] = useState("")
  const [files, setFiles] = useState("")
  const [symbols, setSymbols] = useState("")
  const [constraints, setConstraints] = useState("")
  const [tokenBudget, setTokenBudget] = useState(1800)
  const [brief, setBrief] = useState<TaskMemoryBrief | null>(null)
  const [isLoading, setIsLoading] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const selectedRepoIdRef = useRef(selectedRepoId)
  const requestGenerationRef = useRef(0)
  selectedRepoIdRef.current = selectedRepoId

  useEffect(() => {
    ++requestGenerationRef.current
    setBrief(null)
    setErrorMessage(null)
    setCopied(false)
    setIsLoading(false)
  }, [selectedRepoId])

  const evidenceItems = useMemo(
    () => (brief ? Object.values(brief.sections).flat() : []),
    [brief],
  )

  // A brief is always compiled inside one project. Sending repoId: null asks the server to
  // search nothing, which it refuses — so the request is never made without a project rather
  // than made and refused. The guard is duplicated on the handler because a disabled button is
  // a UI affordance, not a precondition.
  const canPrepare = Boolean(task.trim()) && Boolean(selectedRepoId)

  const prepareBrief = async (checkForChanges = false) => {
    if (!task.trim() || !selectedRepoId) return
    const requestedRepoId = selectedRepoId
    const generation = ++requestGenerationRef.current
    setIsLoading(true)
    setErrorMessage(null)
    setCopied(false)
    try {
      const result = await prepareTaskMemoryBrief({
        task: task.trim(),
        repoId: requestedRepoId,
        tokenBudget,
        files: splitLines(files),
        symbols: splitLines(symbols),
        constraints: splitLines(constraints),
        previousFingerprint: checkForChanges ? brief?.fingerprint : null,
      })
      if (generation !== requestGenerationRef.current || selectedRepoIdRef.current !== requestedRepoId) return
      setBrief(result)
    } catch (error) {
      if (generation !== requestGenerationRef.current || selectedRepoIdRef.current !== requestedRepoId) return
      setErrorMessage(describeApiError(error))
    } finally {
      if (generation === requestGenerationRef.current && selectedRepoIdRef.current === requestedRepoId) {
        setIsLoading(false)
      }
    }
  }

  const copyContext = async () => {
    if (!brief?.context) return
    await navigator.clipboard.writeText(brief.context)
    setCopied(true)
    window.setTimeout(() => setCopied(false), 1800)
  }

  return (
    <motion.div initial="initial" animate="animate" variants={pageTransition} className="space-y-7">
      <div className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
        <div>
          <h1 className="flex items-center gap-3 text-2xl font-semibold text-foreground">
            <ClipboardCheck className="h-6 w-6 text-muted-foreground" />
            Task Brief
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Prepare trusted project context before work begins.
          </p>
        </div>
        {brief ? (
          <Button
            variant="outline"
            onClick={() => void prepareBrief(true)}
            disabled={isLoading || !canPrepare}
            className="w-fit"
          >
            <RefreshCw className={cn("mr-2 h-4 w-4", isLoading && "animate-spin")} />
            Check for changes
          </Button>
        ) : null}
      </div>

      <section className="glass rounded-lg p-5">
        <div className="space-y-2">
          <Label htmlFor="task">Task</Label>
          <Textarea
            id="task"
            value={task}
            onChange={(event) => setTask(event.target.value)}
            placeholder="Fix the authentication callback and add regression coverage"
            className="min-h-24 resize-y"
          />
        </div>

        <div className="mt-5 grid gap-4 md:grid-cols-2">
          <div className="space-y-2">
            <Label htmlFor="files">Files</Label>
            <Input
              id="files"
              value={files}
              onChange={(event) => setFiles(event.target.value)}
              placeholder="src/auth.py, tests/test_auth.py"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="symbols">Symbols</Label>
            <Input
              id="symbols"
              value={symbols}
              onChange={(event) => setSymbols(event.target.value)}
              placeholder="login, validate_session"
            />
          </div>
        </div>

        <div className="mt-4 space-y-2">
          <Label htmlFor="constraints">Constraints</Label>
          <Textarea
            id="constraints"
            value={constraints}
            onChange={(event) => setConstraints(event.target.value)}
            placeholder={"Preserve existing sessions\nDo not change the public response schema"}
            className="min-h-20 resize-y"
          />
        </div>

        <div className="mt-5 grid gap-5 md:grid-cols-[1fr_auto] md:items-end">
          <div className="space-y-3">
            <div className="flex items-center justify-between gap-3">
              <Label htmlFor="token-budget">Context budget</Label>
              <span className="text-sm tabular-nums text-muted-foreground">
                {tokenBudget.toLocaleString()} tokens
              </span>
            </div>
            <Slider
              id="token-budget"
              min={400}
              max={6000}
              step={100}
              value={[tokenBudget]}
              onValueChange={(value) => setTokenBudget(value[0] ?? 1800)}
              aria-label="Context token budget"
            />
          </div>
          <Button onClick={() => void prepareBrief(false)} disabled={isLoading || !canPrepare}>
            <Sparkles className={cn("mr-2 h-4 w-4", isLoading && "animate-pulse")} />
            {isLoading ? "Preparing" : "Prepare brief"}
          </Button>
        </div>

        {!selectedRepoId ? (
          <p className="mt-4 text-sm text-muted-foreground">
            Select a project to prepare a brief. A brief is compiled from one project&apos;s
            memories, so there is nothing to search until one is chosen.
          </p>
        ) : null}
      </section>

      {errorMessage ? (
        <div
          className="rounded-lg border border-destructive/30 bg-destructive/5 p-4 text-sm text-foreground"
          role="alert"
        >
          <div className="flex items-start gap-2">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
            <span>{errorMessage}</span>
          </div>
        </div>
      ) : null}

      {!brief && !isLoading ? (
        <div className="rounded-lg border border-dashed border-border px-5 py-10 text-center">
          <ClipboardCheck className="mx-auto h-6 w-6 text-muted-foreground" />
          <p className="mt-3 text-sm font-medium text-foreground">No task brief prepared</p>
          <p className="mt-1 text-sm text-muted-foreground">
            Enter the work to be done and prepare its evidence.
          </p>
        </div>
      ) : null}

      {brief ? (
        <div className="space-y-5">
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <Metric
              label="Context"
              value={`${brief.tokenCount.toLocaleString()} / ${brief.tokenBudget.toLocaleString()}`}
            />
            <Metric
              label="Evidence"
              value={`${brief.metrics.selectedCount} of ${brief.metrics.candidateCount}`}
            />
            <Metric label="Action" value={brief.taskProfile.action} />
            <Metric label="Fingerprint" value={brief.fingerprint} monospace />
          </div>

          {brief.unchanged ? (
            <StatusBand icon={<Check className="h-4 w-4" />} tone="success">
              The cited context has not changed since the previous brief.
            </StatusBand>
          ) : null}

          {brief.abstained ? (
            <StatusBand icon={<ShieldQuestion className="h-4 w-4" />} tone="warning">
              {brief.abstentionReason ||
                "The brief abstained because it could not find reliable evidence."}
            </StatusBand>
          ) : null}

          {brief.contradictions.length ? (
            <section className="overflow-hidden rounded-lg border border-intent/35 bg-intent/5">
              <div className="flex items-center gap-2 border-b border-intent/20 px-4 py-3">
                <AlertTriangle className="h-4 w-4 text-intent" />
                <h2 className="text-sm font-semibold text-foreground">Contradictions</h2>
              </div>
              <div className="divide-y divide-intent/20">
                {brief.contradictions.map((item) => (
                  <div
                    key={`${item.citation}:${item.otherMemoryId}`}
                    className="px-4 py-3 text-sm"
                  >
                    <span className="font-medium text-foreground">[{item.citation}]</span>{" "}
                    <span className="text-muted-foreground">
                      conflicts with {item.otherMemoryId}: {item.otherSnippet}
                    </span>
                  </div>
                ))}
              </div>
            </section>
          ) : null}

          {brief.unknowns.length ? (
            <section className="rounded-lg border border-border bg-secondary/30 p-4">
              <h2 className="flex items-center gap-2 text-sm font-semibold text-foreground">
                <ShieldQuestion className="h-4 w-4 text-muted-foreground" />
                Unknowns to verify
              </h2>
              <ul className="mt-3 space-y-2 text-sm text-muted-foreground">
                {brief.unknowns.map((unknown) => (
                  <li key={unknown}>- {unknown}</li>
                ))}
              </ul>
            </section>
          ) : null}

          {!brief.unchanged ? (
            <div className="grid gap-5 xl:grid-cols-[minmax(0,1.3fr)_minmax(320px,0.7fr)]">
              <section className="glass min-w-0 overflow-hidden rounded-lg">
                <div className="flex items-center justify-between gap-3 border-b border-border px-4 py-3">
                  <div>
                    <h2 className="text-sm font-semibold text-foreground">Compiled context</h2>
                    <p className="mt-0.5 text-xs text-muted-foreground">
                      Ready for an AI tool or a working session.
                    </p>
                  </div>
                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={() => void copyContext()}
                    aria-label="Copy compiled context"
                  >
                    {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
                  </Button>
                </div>
                <pre className="max-h-[620px] overflow-auto whitespace-pre-wrap break-words p-5 font-mono text-sm leading-6 text-foreground">
                  {brief.context}
                </pre>
              </section>

              <section className="glass min-w-0 overflow-hidden rounded-lg">
                <div className="flex items-center gap-2 border-b border-border px-4 py-3">
                  <FileCode2 className="h-4 w-4 text-muted-foreground" />
                  <h2 className="text-sm font-semibold text-foreground">Evidence</h2>
                </div>
                {evidenceItems.length ? (
                  <div className="divide-y divide-border">
                    {evidenceItems.map((item) => (
                      <div key={item.citation} className="p-4">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="rounded bg-accent px-2 py-0.5 text-xs font-semibold text-highlight">
                            {item.citation}
                          </span>
                          <span className="text-xs text-muted-foreground">
                            {SECTION_LABELS[sectionFor(brief, item.id)]}
                          </span>
                          {item.retrievalChannels.map((channel) => (
                            <span
                              key={`${item.citation}:${channel}`}
                              className="rounded bg-secondary px-2 py-0.5 text-xs text-muted-foreground"
                            >
                              {channel}
                            </span>
                          ))}
                          {typeof item.confidence === "number" ? (
                            <span className="ml-auto text-xs tabular-nums text-muted-foreground">
                              {Math.round(item.confidence * 100)}%
                            </span>
                          ) : null}
                        </div>
                        <p className="mt-2 text-sm leading-6 text-foreground">{item.content}</p>
                        {item.files.length || item.symbols.length ? (
                          <p className="mt-2 break-words text-xs text-muted-foreground">
                            {[...item.files, ...item.symbols].join(" | ")}
                          </p>
                        ) : null}
                        {item.retrievalChannels.length ? (
                          <p className="mt-2 text-xs tabular-nums text-muted-foreground">
                            Direct {formatScore(item.retrievalFactors.directScore)} | Graph{" "}
                            {formatScore(item.retrievalFactors.graphScore)} | Fusion{" "}
                            {formatScore(item.retrievalFactors.rrfScore)}
                          </p>
                        ) : null}
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="p-5 text-sm text-muted-foreground">
                    No evidence was selected for this brief.
                  </p>
                )}
              </section>
            </div>
          ) : null}
        </div>
      ) : null}
    </motion.div>
  )
}

function sectionFor(brief: TaskMemoryBrief, itemId: string): TaskBriefSectionName {
  return (
    (Object.keys(brief.sections) as TaskBriefSectionName[]).find((section) =>
      brief.sections[section].some((item) => item.id === itemId),
    ) || "knowledge"
  )
}

function formatScore(value?: number): string {
  return typeof value === "number" ? value.toFixed(3) : "0.000"
}

function Metric({
  label,
  value,
  monospace = false,
}: {
  label: string
  value: string
  monospace?: boolean
}) {
  return (
    <div className="glass min-w-0 rounded-lg p-4">
      <p className="text-xs font-medium uppercase text-muted-foreground">{label}</p>
      <p
        className={cn(
          "mt-2 truncate text-base font-semibold capitalize text-foreground",
          monospace && "font-mono text-sm normal-case",
        )}
        title={value}
      >
        {value}
      </p>
    </div>
  )
}

function StatusBand({
  icon,
  tone,
  children,
}: {
  icon: ReactNode
  tone: "success" | "warning"
  children: ReactNode
}) {
  return (
    <div
      className={cn(
        "flex items-start gap-2 rounded-lg border p-4 text-sm",
        tone === "success"
          ? "border-success/30 bg-success/5 text-success"
          : "border-intent/30 bg-intent/5 text-intent",
      )}
    >
      <span className="mt-0.5 shrink-0">{icon}</span>
      <span>{children}</span>
    </div>
  )
}
