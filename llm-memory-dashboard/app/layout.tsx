import type React from "react"
import type { Metadata } from "next"
import { ThemeProvider } from "@/components/providers/theme-provider"
import { AppLayout } from "@/components/layout/app-layout"
import "./globals.css"

export const metadata: Metadata = {
  title: "LLM Memory Dashboard",
  description: "Visualize and manage your LLM memory system",
  generator: "v0.app",
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className="font-sans antialiased">
        <ThemeProvider attribute="class" defaultTheme="system" enableSystem disableTransitionOnChange>
          <AppLayout>{children}</AppLayout>
        </ThemeProvider>
      </body>
    </html>
  )
}
