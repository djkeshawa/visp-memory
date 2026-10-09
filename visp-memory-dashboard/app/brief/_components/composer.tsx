"use client"

import type { FormEvent } from "react"
import { Sparkles } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Slider } from "@/components/ui/slider"
import { Textarea } from "@/components/ui/textarea"
import { cn } from "@/lib/utils"

export interface ComposerValues { task: string; files: string; symbols: string; constraints: string; tokenBudget: number }

const optional = <span className="font-normal text-muted-foreground"> · optional</span>

/** The task description and the limits a brief is compiled within. */
export function Composer({ values, onChange, canCompile, isLoading, hasProject, onCompile }: {
  values: ComposerValues
  onChange: (patch: Partial<ComposerValues>) => void
  canCompile: boolean
  isLoading: boolean
  hasProject: boolean
  onCompile: () => void
}) {
  const submit = (event: FormEvent) => { event.preventDefault(); onCompile() }
  return (
    <form aria-label="Brief composer" onSubmit={submit} className="surface flex min-w-0 flex-1 basis-80 flex-col gap-5 rounded-2xl p-5 sm:p-6">
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="task" className="font-semibold">Task</Label>
        <Textarea id="task" value={values.task} onChange={(event) => onChange({ task: event.target.value })} placeholder="Fix the authentication callback and add regression coverage" className="min-h-28 resize-y rounded-xl" />
      </div>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="files" className="font-semibold">Files{optional}</Label>
        <Input id="files" value={values.files} onChange={(event) => onChange({ files: event.target.value })} placeholder="src/auth.py, tests/test_auth.py" className="font-mono text-[13px]" />
      </div>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="symbols" className="font-semibold">Symbols{optional}</Label>
        <Input id="symbols" value={values.symbols} onChange={(event) => onChange({ symbols: event.target.value })} placeholder="login, validate_session" className="font-mono text-[13px]" />
      </div>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="constraints" className="font-semibold">Constraints{optional}</Label>
        <Textarea id="constraints" value={values.constraints} onChange={(event) => onChange({ constraints: event.target.value })} placeholder={"Preserve existing sessions\nDo not change the public response schema"} className="min-h-20 resize-y rounded-xl" />
      </div>
      <div className="flex flex-col gap-2.5">
        <div className="flex items-center justify-between gap-3">
          <Label id="token-budget-label" className="font-semibold">Token budget</Label>
          <span className="font-mono text-[13px] tabular-nums">{values.tokenBudget.toLocaleString()}</span>
        </div>
        <Slider id="token-budget" min={400} max={6000} step={100} value={[values.tokenBudget]} onValueChange={(value) => onChange({ tokenBudget: value[0] ?? 1800 })} aria-labelledby="token-budget-label" />
      </div>
      <Button type="submit" size="lg" disabled={isLoading || !canCompile}>
        <Sparkles className={cn("h-4 w-4", isLoading && "animate-pulse")} aria-hidden="true" />
        {isLoading ? "Compiling" : "Compile brief"}
      </Button>
      {!hasProject ? <p className="text-sm text-muted-foreground">Select a project to compile a brief. A brief is built from one project&apos;s memories, so there is nothing to search until one is chosen.</p> : null}
    </form>
  )
}
