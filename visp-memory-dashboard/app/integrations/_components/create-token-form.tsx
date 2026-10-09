import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import type { ProjectScope } from "@/lib/types"
import { cn } from "@/lib/utils"

export const AVAILABLE_SCOPES = [
  ["memory:read", "Read memories"],
  ["memory:write", "Create and update memories"],
  ["intent:read", "Read intents"],
  ["intent:write", "Create and complete intents"],
  ["project:read", "Read project metadata"],
] as const

interface CreateTokenFormProps {
  name: string
  scopes: string[]
  repoIds: string[]
  projects: ProjectScope[]
  saving: boolean
  onName: (value: string) => void
  onToggleScope: (scope: string) => void
  onToggleProject: (id: string) => void
  onSubmit: () => void
}

export function CreateTokenForm({ name, scopes, repoIds, projects, saving, onName, onToggleScope, onToggleProject, onSubmit }: CreateTokenFormProps) {
  return (
    <form onSubmit={(event) => { event.preventDefault(); onSubmit() }} aria-labelledby="create-token-heading" className="surface flex min-w-0 flex-[1_1_20rem] flex-col gap-4 rounded-2xl p-5">
      <h2 id="create-token-heading" className="text-[15px] font-semibold">Create access token</h2>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="token-name" className="font-semibold">Token name</Label>
        <Input id="token-name" value={name} onChange={(event) => onName(event.target.value)} placeholder="Codex workspace" />
      </div>
      <fieldset className="flex flex-col gap-1">
        <legend className="pb-1.5 text-sm font-semibold">Permissions</legend>
        {AVAILABLE_SCOPES.map(([scope, label]) => (
          <label key={scope} className="flex min-h-10 items-center gap-2.5 text-sm">
            <input type="checkbox" className="h-[18px] w-[18px]" checked={scopes.includes(scope)} onChange={() => onToggleScope(scope)} />
            <span>{label} <code className="ml-1 font-mono text-xs text-muted-foreground">{scope}</code></span>
          </label>
        ))}
      </fieldset>
      <fieldset className="flex flex-col gap-1">
        <legend className="pb-1.5 text-sm font-semibold">Project access</legend>
        <div className="flex max-h-56 flex-wrap gap-1.5 overflow-y-auto">
          {projects.map((project) => (
            <label key={project.id} className={cn("inline-flex h-10 items-center gap-2 rounded-full border px-3 text-[13px]", repoIds.includes(project.id) ? "border-input bg-secondary" : "border-border text-muted-foreground")}>
              <input type="checkbox" checked={repoIds.includes(project.id)} onChange={() => onToggleProject(project.id)} />
              <span className="max-w-[12rem] truncate">{project.name}</span>
            </label>
          ))}
        </div>
        <span className="text-xs text-muted-foreground">No selection means every project currently available to your account.</span>
      </fieldset>
      <Button type="submit" size="lg" className="h-11" disabled={saving}>{saving ? "Creating..." : "Create token"}</Button>
    </form>
  )
}
