"use client"

import { Sidebar } from "./sidebar"
import { Suspense, type ReactNode } from "react"
import { usePathname } from "next/navigation"
import { SelectedProjectProvider } from "@/lib/project-selection"

interface AppLayoutProps {
  children: ReactNode
}

export function AppLayout({ children }: AppLayoutProps) {
  const pathname = usePathname()

  if (pathname.endsWith("/auth")) {
    return <main className="min-h-screen bg-background p-4 md:p-8">{children}</main>
  }

  return (
    <SelectedProjectProvider>
      <div className="min-h-screen overflow-x-hidden bg-background">
        <Suspense fallback={null}>
          <Sidebar />
        </Suspense>
        <main className="min-h-screen min-w-0 p-4 md:ml-60 md:p-8">
          <div className="mx-auto min-w-0 max-w-[1440px]">{children}</div>
        </main>
      </div>
    </SelectedProjectProvider>
  )
}
