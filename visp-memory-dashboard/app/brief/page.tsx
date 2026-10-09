"use client"

import { useEffect, useRef, useState } from "react"
import { AlertTriangle, ClipboardCheck } from "lucide-react"
import { PageHeader } from "@/components/strata/primitives"
import { describeApiError, prepareTaskMemoryBrief } from "@/lib/api"
import { useSelectedProjectId } from "@/lib/project-selection"
import type { TaskMemoryBrief } from "@/lib/types"
import { BriefResult } from "./_components/brief-result"
import { Composer, type ComposerValues } from "./_components/composer"

function splitLines(value: string): string[] {
  return value.split(/[\n,]/).map((item) => item.trim()).filter(Boolean)
}

const INITIAL: ComposerValues = { task: "", files: "", symbols: "", constraints: "", tokenBudget: 1800 }

export default function TaskBriefPage() {
  const selectedRepoId = useSelectedProjectId()
  const [values, setValues] = useState<ComposerValues>(INITIAL)
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

  // A brief is always compiled inside one project. Sending repoId: null asks the server to
  // search nothing, which it refuses — so the request is never made without a project rather
  // than made and refused. The guard is duplicated on the handler because a disabled button is
  // a UI affordance, not a precondition.
  const canPrepare = Boolean(values.task.trim()) && Boolean(selectedRepoId)

  const prepareBrief = async (checkForChanges = false) => {
    if (!values.task.trim() || !selectedRepoId) return
    const requestedRepoId = selectedRepoId
    const generation = ++requestGenerationRef.current
    setIsLoading(true)
    setErrorMessage(null)
    setCopied(false)
    try {
      const result = await prepareTaskMemoryBrief({
        task: values.task.trim(),
        repoId: requestedRepoId,
        tokenBudget: values.tokenBudget,
        files: splitLines(values.files),
        symbols: splitLines(values.symbols),
        constraints: splitLines(values.constraints),
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
    try {
      await navigator.clipboard.writeText(brief.context)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1800)
    } catch {
      setErrorMessage("Copying was blocked by the browser. Open “Compiled context text” and select it instead.")
    }
  }

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <PageHeader
        eyebrow="Gather trusted context before work begins"
        title="Task brief"
        description="Notes saved through the dashboard or API stay available in Recall but are excluded here. To add context you have verified, use the local CLI to record a reviewed conclusion with its source."
      />
      <div className="flex flex-wrap items-start gap-6">
        <Composer
          values={values}
          onChange={(patch) => setValues((current) => ({ ...current, ...patch }))}
          canCompile={canPrepare}
          isLoading={isLoading}
          hasProject={Boolean(selectedRepoId)}
          onCompile={() => void prepareBrief(false)}
        />
        <div className="flex min-w-0 flex-[999_1_30rem] flex-col gap-4">
          {errorMessage ? (
            <div role="alert" className="flex items-start gap-2 rounded-xl border border-destructive/30 bg-destructive/5 p-4 text-sm">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" aria-hidden="true" />
              <span>{errorMessage}</span>
            </div>
          ) : null}
          {brief ? (
            <BriefResult brief={brief} isLoading={isLoading} canCheck={canPrepare} copied={copied} onCheck={() => void prepareBrief(true)} onCopy={() => void copyContext()} />
          ) : (
            <div className="rounded-2xl border border-dashed border-border px-5 py-12 text-center">
              <ClipboardCheck className="mx-auto h-6 w-6 text-muted-foreground" aria-hidden="true" />
              {isLoading ? (
                <p role="status" className="mt-3 text-sm font-medium">Compiling brief…</p>
              ) : (
                <>
                  <p className="mt-3 text-sm font-medium">No task brief compiled</p>
                  <p className="mt-1 text-sm text-muted-foreground">Enter the work to be done and compile its evidence.</p>
                </>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
