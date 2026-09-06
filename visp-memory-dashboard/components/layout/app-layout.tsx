"use client"

import { Sidebar } from "./sidebar"
import { AuthGate } from "./auth-gate"
import { Suspense, type ReactNode } from "react"
import { usePathname } from "next/navigation"
import { SelectedProjectProvider } from "@/lib/project-selection"

interface AppLayoutProps {
  children: ReactNode
}

export function AppLayout({ children }: AppLayoutProps) {
  const pathname = usePathname()

  if (pathname.replace(/\/+$/, "").endsWith("/auth")) {
    return <main className="min-h-screen bg-background p-4 md:p-8">{children}</main>
  }

  return (
    <AuthGate>
      <SelectedProjectProvider>
        <div className="min-h-screen overflow-x-hidden bg-background">
          <a href="#main-content" className="fixed left-4 top-4 z-[100] -translate-y-24 rounded-lg bg-primary px-4 py-3 text-primary-foreground focus:translate-y-0">Skip to content</a>
          <Suspense fallback={null}>
            <Sidebar />
          </Suspense>
          <main id="main-content" className="min-h-screen min-w-0 p-5 md:ml-64 md:p-8 xl:px-12 xl:py-10">
            <div className="mx-auto min-w-0 max-w-[1440px]">{children}</div>
          </main>
        </div>
      </SelectedProjectProvider>
    </AuthGate>
  )
}
