import type { FormEvent } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { cn } from "@/lib/utils"

export type AccountRole = "admin" | "user"

interface CreateAccountFormProps {
  username: string
  displayName: string
  password: string
  role: AccountRole
  onUsername: (value: string) => void
  onDisplayName: (value: string) => void
  onPassword: (value: string) => void
  onRole: (value: AccountRole) => void
  onSubmit: (event: FormEvent) => void
}

const ROLES: { value: AccountRole; label: string }[] = [
  { value: "user", label: "Member" },
  { value: "admin", label: "Admin" },
]

export function CreateAccountForm({ username, displayName, password, role, onUsername, onDisplayName, onPassword, onRole, onSubmit }: CreateAccountFormProps) {
  return (
    <form onSubmit={onSubmit} aria-labelledby="create-account-heading" className="surface flex min-w-0 flex-[1_1_18rem] flex-col gap-3.5 rounded-2xl p-5">
      <h2 id="create-account-heading" className="text-[15px] font-semibold">Create account</h2>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="new-username" className="font-semibold">Username</Label>
        <Input id="new-username" value={username} onChange={(event) => onUsername(event.target.value)} minLength={3} placeholder="sam" required className="font-mono text-[13px]" />
      </div>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="new-display-name" className="font-semibold">Display name</Label>
        <Input id="new-display-name" value={displayName} onChange={(event) => onDisplayName(event.target.value)} />
      </div>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="new-password" className="font-semibold">Temporary password</Label>
        <Input id="new-password" type="password" value={password} onChange={(event) => onPassword(event.target.value)} minLength={12} autoComplete="new-password" required />
        <p className="text-xs text-muted-foreground">At least 12 characters. Share it with the person directly.</p>
      </div>
      <fieldset className="flex flex-col gap-1.5">
        <legend className="pb-1.5 text-sm font-semibold">Role</legend>
        <div className="flex gap-1.5">
          {ROLES.map((option) => (
            <label key={option.value} className={cn("inline-flex h-11 flex-1 items-center gap-2 rounded-xl border px-3 text-[13px]", role === option.value ? "border-input bg-secondary" : "border-border text-muted-foreground")}>
              <input type="radio" name="role" value={option.value} checked={role === option.value} onChange={() => onRole(option.value)} />
              {option.label}
            </label>
          ))}
        </div>
      </fieldset>
      <Button type="submit" size="lg" className="h-11">Create account</Button>
    </form>
  )
}
