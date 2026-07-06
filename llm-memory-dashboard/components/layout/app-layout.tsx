import { Sidebar } from "./sidebar"
import { Suspense, type ReactNode } from "react"
import { SelectedProjectProvider } from "@/lib/project-selection"

interface AppLayoutProps {
  children: ReactNode
}

export function AppLayout({ children }: AppLayoutProps) {
  return (
    <SelectedProjectProvider>
      <div className="min-h-screen overflow-x-hidden bg-background">
        <Suspense fallback={null}>
          <Sidebar />
        </Suspense>
        <main className="min-h-screen p-4 md:ml-60 md:p-8">
          <div className="mx-auto max-w-6xl">{children}</div>
        </main>
      </div>
    </SelectedProjectProvider>
  )
}
