import Link from "next/link"
import {
  Activity, Cable, ClipboardCheck, ClipboardList, FolderGit2, LayoutDashboard,
  Library, Moon, Network, Search, Settings, Target, Users,
} from "lucide-react"
import { projectHref } from "@/lib/project-selection"
import { cn } from "@/lib/utils"

const groups = [
  {
    label: "Knowledge",
    items: [
      { href: "/", label: "Overview", icon: LayoutDashboard },
      { href: "/brief", label: "Task Brief", icon: ClipboardCheck },
      { href: "/memories", label: "Memories", icon: Library },
      { href: "/recall", label: "Recall", icon: Search },
      { href: "/graph", label: "Memory Graph", icon: Network },
      { href: "/intents", label: "Intents", icon: Target },
    ],
  },
  {
    label: "Workspace",
    items: [
      { href: "/projects", label: "Projects", icon: FolderGit2 },
      { href: "/intelligence", label: "Intelligence", icon: ClipboardList },
      { href: "/dreaming", label: "Dreaming", icon: Moon },
    ],
  },
  {
    label: "Manage",
    items: [
      { href: "/health", label: "Operations", icon: Activity },
      { href: "/integrations", label: "Integrations", icon: Cable },
      { href: "/users", label: "Users", icon: Users },
      { href: "/settings", label: "Settings", icon: Settings },
    ],
  },
]

export function Navigation({ activePath, repoId }: { activePath: string; repoId: string | null }) {
  return groups.map((group) => (
    <div key={group.label}>
      <p className="px-3 pb-2 pt-5 text-[11px] font-semibold tracking-wide text-muted-foreground">{group.label}</p>
      <div className="space-y-0.5">
        {group.items.map((item) => (
          <Link
            key={item.href}
            aria-current={activePath === item.href ? "page" : undefined}
            href={projectHref(item.href, repoId)}
            className={cn(
              "flex items-center gap-3 rounded-xl px-3 py-2 text-[13px] font-medium transition-colors duration-150",
              activePath === item.href
                ? "bg-accent text-accent-foreground"
                : "text-muted-foreground hover:bg-secondary hover:text-foreground",
            )}
          >
            <item.icon className="h-[18px] w-[18px] shrink-0" aria-hidden="true" />
            <span>{item.label}</span>
          </Link>
        ))}
      </div>
    </div>
  ))
}
