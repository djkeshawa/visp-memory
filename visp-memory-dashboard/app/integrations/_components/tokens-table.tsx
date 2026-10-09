import { Button } from "@/components/ui/button"
import type { PersonalAccessToken, ProjectScope } from "@/lib/types"

const HEAD = "px-3 py-2.5 text-xs font-medium text-muted-foreground"

interface TokensTableProps {
  tokens: PersonalAccessToken[]
  projects: ProjectScope[]
  loading: boolean
  onRevoke: (tokenId: string) => void
}

function projectsLabel(token: PersonalAccessToken, projects: ProjectScope[]): string {
  if (token.repoIds.length === 0) return "All projects"
  return token.repoIds.map((id) => projects.find((project) => project.id === id)?.name ?? id).join(", ")
}

export function TokensTable({ tokens, projects, loading, onRevoke }: TokensTableProps) {
  return (
    <section aria-labelledby="active-tokens-heading" className="surface min-w-0 flex-[999_1_32rem] overflow-hidden rounded-2xl">
      <div className="px-5 py-4 sm:px-6"><h2 id="active-tokens-heading" className="text-[15px] font-semibold">Active tokens · {tokens.length}</h2></div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[38rem] border-collapse text-left text-sm">
          <thead>
            <tr>
              <th scope="col" className={`${HEAD} pl-5 sm:pl-6`}>Name</th>
              <th scope="col" className={HEAD}>Permissions</th>
              <th scope="col" className={HEAD}>Projects</th>
              <th scope="col" className={HEAD}>Last used</th>
              <th scope="col" className={`${HEAD} pr-5 text-right sm:pr-6`}>Actions</th>
            </tr>
          </thead>
          <tbody>
            {tokens.map((token) => (
              <tr key={token.id} className="border-t border-border">
                <td className="py-3.5 pl-5 pr-3 align-top sm:pl-6">
                  <span className="block font-semibold">{token.name}</span>
                  <code className="font-mono text-xs text-muted-foreground">{token.tokenPrefix}…</code>
                </td>
                <td className="max-w-[14rem] break-words px-3 py-3.5 align-top text-[13px] text-muted-foreground">{token.scopes.join(", ")}</td>
                <td className="max-w-[10rem] break-words px-3 py-3.5 align-top text-[13px] text-muted-foreground">{projectsLabel(token, projects)}</td>
                <td className="whitespace-nowrap px-3 py-3.5 align-top text-[13px] text-muted-foreground">{token.lastUsedAt ? new Date(token.lastUsedAt).toLocaleString() : "Never"}</td>
                <td className="py-2 pl-3 pr-5 text-right align-top sm:pr-6">
                  <Button size="sm" variant="outline" className="h-10 border-destructive/50 text-destructive hover:bg-destructive/10 hover:text-destructive" onClick={() => onRevoke(token.id)} aria-label={`Revoke ${token.name}`}>Revoke</Button>
                </td>
              </tr>
            ))}
            {!loading && tokens.length === 0 ? <tr className="border-t border-border"><td colSpan={5} className="px-4 py-8 text-center text-muted-foreground">No active integration tokens.</td></tr> : null}
          </tbody>
        </table>
      </div>
    </section>
  )
}
