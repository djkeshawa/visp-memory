"use client"

import { useEffect, useRef, useState, type FormEvent } from "react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import { BookOpen, Search } from "lucide-react"
import { QuickActions } from "@/components/dashboard/quick-actions"
import { stashRecallQuery } from "@/components/recall/recall-handoff"
import { projectHref } from "@/lib/project-selection"

function isTyping(target: EventTarget | null): boolean {
  const element = target as HTMLElement | null
  return !!element && (element.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(element.tagName))
}

/** The Desk's top row: ask the project a question, brief a task, or add a memory. */
export function DeskSearch({ repoId, onMemoryCreated }: { repoId: string | null; onMemoryCreated: () => void }) {
  const router = useRouter()
  const inputRef = useRef<HTMLInputElement>(null)
  const [query, setQuery] = useState("")

  useEffect(() => {
    const focusOnSlash = (event: KeyboardEvent) => {
      if (event.key !== "/" || event.metaKey || event.ctrlKey || event.altKey || isTyping(event.target)) return
      event.preventDefault()
      inputRef.current?.focus()
    }
    window.addEventListener("keydown", focusOnSlash)
    return () => window.removeEventListener("keydown", focusOnSlash)
  }, [])

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!query.trim()) return
    stashRecallQuery(query)
    router.push(projectHref("/recall", repoId))
  }

  return (
    <div className="flex flex-wrap items-center gap-3">
      <form role="search" onSubmit={submit} className="surface flex h-12 min-w-0 flex-[1_1_320px] items-center gap-2.5 rounded-xl pl-4 pr-2 focus-within:border-ring focus-within:ring-2 focus-within:ring-ring">
        <Search className="h-[18px] w-[18px] shrink-0 text-muted-foreground" aria-hidden="true" />
        <input
          ref={inputRef}
          id="desk-recall"
          type="search"
          aria-label="Recall a memory"
          aria-keyshortcuts="/"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Ask what this project knows"
          className="h-full min-w-0 flex-1 bg-transparent text-[15px] outline-none placeholder:text-muted-foreground"
        />
        <kbd className="hidden rounded-md border border-border px-1.5 py-0.5 font-mono text-xs text-muted-foreground sm:inline">/</kbd>
      </form>
      <Link href={projectHref("/brief", repoId)} className="inline-flex h-12 items-center gap-2 rounded-xl border border-border px-4 text-sm font-medium transition-colors hover:bg-secondary">
        <BookOpen className="h-4 w-4" aria-hidden="true" />
        Brief a task
      </Link>
      <QuickActions onMemoryCreated={onMemoryCreated} />
    </div>
  )
}
