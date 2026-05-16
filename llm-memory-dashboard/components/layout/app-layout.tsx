import { Sidebar } from "./sidebar"
import type { ReactNode } from "react"

interface AppLayoutProps {
  children: ReactNode
}

export function AppLayout({ children }: AppLayoutProps) {
  return (
    <div className="min-h-screen overflow-x-hidden bg-background">
      <Sidebar />
      <main className="min-h-screen p-4 md:ml-60 md:p-8">
        <div className="mx-auto max-w-6xl">{children}</div>
      </main>
    </div>
  )
}
