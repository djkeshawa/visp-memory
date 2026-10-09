import { Button } from "@/components/ui/button"
import { Pill } from "@/components/strata/primitives"
import type { AuthUser } from "@/lib/types"

const HEAD = "px-3 py-2.5 text-xs font-medium text-muted-foreground"

function initials(user: AuthUser): string {
  const words = (user.displayName || user.username).trim().split(/\s+/).filter(Boolean)
  const letters = words.length > 1 ? words[0][0] + words[1][0] : (words[0] ?? "?").slice(0, 2)
  return letters.toUpperCase()
}

function lastSignIn(user: AuthUser): string {
  if (user.lastLoginAt) return new Date(user.lastLoginAt).toLocaleString()
  return "Never"
}

export function AccountsTable({ users, onToggle }: { users: AuthUser[]; onToggle: (user: AuthUser) => void }) {
  return (
    <section aria-labelledby="accounts-heading" className="surface min-w-0 flex-[999_1_32rem] overflow-hidden rounded-2xl">
      <div className="px-5 py-4 sm:px-6"><h2 id="accounts-heading" className="text-[15px] font-semibold">Accounts · {users.length}</h2></div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[36rem] border-collapse text-left text-sm">
          <thead>
            <tr>
              <th scope="col" className={`${HEAD} pl-5 sm:pl-6`}>User</th>
              <th scope="col" className={HEAD}>Role</th>
              <th scope="col" className={HEAD}>Access</th>
              <th scope="col" className={`${HEAD} pr-5 sm:pr-6`}>Last sign-in</th>
            </tr>
          </thead>
          <tbody>
            {users.map((user) => (
              <tr key={user.id} className="border-t border-border">
                <td className="py-3.5 pl-5 pr-3 sm:pl-6">
                  <div className="flex items-center gap-3">
                    <span aria-hidden="true" className="flex h-[34px] w-[34px] shrink-0 items-center justify-center rounded-full bg-secondary text-[13px] font-semibold">{initials(user)}</span>
                    <span className="flex min-w-0 flex-col">
                      <span className="font-semibold">{user.displayName || user.username}</span>
                      <span className="font-mono text-xs text-muted-foreground">{user.username}</span>
                    </span>
                  </div>
                </td>
                <td className="px-3 py-3.5"><Pill tone={user.role === "admin" ? "info" : "neutral"}>{user.role === "admin" ? "Admin" : "Member"}</Pill></td>
                <td className="px-3 py-3.5">
                  <div className="flex items-center gap-2.5">
                    <Pill tone={user.enabled ? "success" : "neutral"}>{user.enabled ? "Enabled" : "Disabled"}</Pill>
                    <Button size="sm" variant="outline" className="h-10" onClick={() => onToggle(user)} aria-label={`${user.enabled ? "Disable" : "Enable"} ${user.username}`}>{user.enabled ? "Disable" : "Enable"}</Button>
                  </div>
                </td>
                <td className="whitespace-nowrap py-3.5 pl-3 pr-5 text-[13px] text-muted-foreground sm:pr-6">{lastSignIn(user)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}
