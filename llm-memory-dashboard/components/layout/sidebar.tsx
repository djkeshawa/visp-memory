"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import { Brain, LayoutDashboard, Network, Search, Target } from "lucide-react"
import { cn } from "@/lib/utils"
import { ThemeToggle } from "@/components/ui/theme-toggle"

const navItems = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/graph", label: "Memory Graph", icon: Network },
  { href: "/recall", label: "Recall", icon: Search },
  { href: "/intents", label: "Intents", icon: Target },
]

export function Sidebar() {
  const pathname = usePathname()
  const activePath = pathname === "/dashboard" ? "/" : pathname.replace(/^\/dashboard/, "")

  return (
    <aside className="sticky top-0 z-40 flex w-full flex-col border-b border-border bg-card md:fixed md:left-0 md:top-0 md:h-screen md:w-60 md:border-b-0 md:border-r">
      {/* Logo */}
      <div className="flex items-center gap-3 border-b border-border px-4 py-4 md:px-6 md:py-5">
        <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-blue-500 to-indigo-600">
          <Brain className="h-5 w-5 text-white" />
        </div>
        <div>
          <h1 className="font-semibold text-foreground">LLM Memory</h1>
          <p className="text-xs text-muted-foreground">Dashboard</p>
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex gap-1 overflow-x-auto px-3 py-3 md:block md:flex-1 md:space-y-1 md:overflow-visible md:py-4">
        {navItems.map((item) => {
          const isActive = activePath === item.href
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "flex shrink-0 items-center gap-2 rounded-lg px-3 py-2.5 text-sm font-medium transition-all duration-200 md:gap-3 md:px-4",
                isActive
                  ? "bg-gradient-to-r from-blue-500 to-indigo-600 text-white shadow-md"
                  : "text-muted-foreground hover:bg-secondary hover:text-foreground",
              )}
            >
              <item.icon className="h-5 w-5" />
              {item.label}
            </Link>
          )
        })}
      </nav>

      {/* Bottom Section */}
      <div className="hidden space-y-3 border-t border-border px-3 py-4 md:block">
        <div className="flex items-center justify-between px-4">
          <span className="text-sm text-muted-foreground">Theme</span>
          <ThemeToggle />
        </div>

        {/* Status Indicator */}
        <div className="flex items-center gap-2 px-4 py-2">
          <div className="h-2 w-2 rounded-full bg-success animate-pulse" />
          <span className="text-xs text-muted-foreground">System Online</span>
        </div>
      </div>
    </aside>
  )
}
