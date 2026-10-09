import Link from "next/link"
import { projectHref } from "@/lib/project-selection"
import { cn } from "@/lib/utils"

/** Know what the project knows, curate it, operate the server. Routes are unchanged. */
export const NAV_GROUPS = [
  {
    label: "Know",
    items: [
      { href: "/", label: "Desk" },
      { href: "/recall", label: "Recall" },
      { href: "/brief", label: "Task brief" },
      { href: "/memories", label: "Library" },
      { href: "/graph", label: "Graph" },
    ],
  },
  {
    label: "Curate",
    items: [
      { href: "/dreaming", label: "Review" },
      { href: "/intents", label: "Intents" },
      { href: "/intelligence", label: "Intelligence" },
    ],
  },
  {
    label: "Operate",
    items: [
      { href: "/projects", label: "Projects" },
      { href: "/health", label: "Operations" },
      { href: "/integrations", label: "Integrations" },
      { href: "/users", label: "Users" },
      { href: "/settings", label: "Settings" },
    ],
  },
] as const

export function Navigation({ activePath, repoId }: { activePath: string; repoId: string | null }) {
  return NAV_GROUPS.map((group) => (
    <div key={group.label} className="pt-5 first:pt-3">
      <p className="eyebrow px-3 pb-1.5 text-[11px]">{group.label}</p>
      <div className="space-y-0.5">
        {group.items.map((item) => {
          const current = activePath === item.href
          return (
            <Link
              key={item.href}
              aria-current={current ? "page" : undefined}
              href={projectHref(item.href, repoId)}
              className={cn(
                "flex min-h-9 items-center rounded-lg px-3 text-sm transition-colors duration-150",
                current
                  ? "bg-sidebar-accent font-semibold text-sidebar-accent-foreground"
                  : "font-medium text-muted-foreground hover:bg-sidebar-accent/60 hover:text-foreground",
              )}
            >
              {item.label}
            </Link>
          )
        })}
      </div>
    </div>
  ))
}
