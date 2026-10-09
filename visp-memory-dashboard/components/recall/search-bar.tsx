"use client"

import type { FormEvent } from "react"
import { Search } from "lucide-react"
import { Button } from "@/components/ui/button"

interface SearchBarProps {
  value: string
  onChange: (value: string) => void
  onSearch: (query: string) => void
  isLoading?: boolean
}

export function SearchBar({ value, onChange, onSearch, isLoading }: SearchBarProps) {
  const handleSubmit = (event: FormEvent) => {
    event.preventDefault()
    if (value.trim()) onSearch(value.trim())
  }

  return (
    <form role="search" onSubmit={handleSubmit} className="surface flex h-14 items-center gap-2.5 rounded-[14px] border-input pl-4 pr-2 focus-within:border-ring focus-within:ring-2 focus-within:ring-ring">
      <Search className="h-5 w-5 shrink-0 text-muted-foreground" aria-hidden="true" />
      <input
        type="search"
        aria-label="Search memories"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder="What did I decide about authentication?"
        className="h-full min-w-0 flex-1 bg-transparent text-base outline-none placeholder:text-muted-foreground sm:text-[17px]"
      />
      <Button type="submit" disabled={isLoading || !value.trim()} className="h-11 rounded-[10px] px-4 font-semibold sm:px-5">
        {isLoading ? "Searching..." : "Search"}
      </Button>
    </form>
  )
}
