"use client"

import { useEffect, useState } from "react"
import { useTheme } from "next-themes"
import { cn } from "@/lib/utils"

const OPTIONS = [
  { value: "system", label: "System" },
  { value: "dark", label: "Dark" },
  { value: "light", label: "Light" },
] as const

export function AppearanceSection() {
  const { theme, setTheme } = useTheme()
  const [mounted, setMounted] = useState(false)
  useEffect(() => setMounted(true), [])
  return (
    <section aria-labelledby="appearance-heading" className="surface rounded-2xl p-5 sm:p-6">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="appearance-heading" className="text-lg font-semibold">Appearance</h2>
        <span className="text-xs text-muted-foreground">Saved in this browser only</span>
      </div>
      <div role="group" aria-label="Theme" className="mt-4 inline-flex rounded-xl bg-secondary p-1">
        {OPTIONS.map((option) => {
          const pressed = mounted && theme === option.value
          return (
            <button
              key={option.value}
              type="button"
              aria-pressed={pressed}
              onClick={() => setTheme(option.value)}
              className={cn(
                "h-10 min-w-20 rounded-lg px-4 text-sm font-medium transition-colors",
                pressed ? "bg-card text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground",
              )}
            >
              {option.label}
            </button>
          )
        })}
      </div>
    </section>
  )
}
