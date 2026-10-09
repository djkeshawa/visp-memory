import type React from "react"
import type { Metadata } from "next"
import { Instrument_Sans, JetBrains_Mono } from "next/font/google"
import { ThemeProvider } from "@/components/providers/theme-provider"
import { AppLayout } from "@/components/layout/app-layout"
import "./globals.css"

const sans = Instrument_Sans({ subsets: ["latin"], variable: "--font-instrument-sans", display: "swap" })
const mono = JetBrains_Mono({ subsets: ["latin"], variable: "--font-jetbrains-mono", display: "swap" })

export const metadata: Metadata = {
  title: "Visp Memory",
  description: "Cited, evidence-first project memory",
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  return (
    <html lang="en" suppressHydrationWarning className={`${sans.variable} ${mono.variable}`}>
      <body className="font-sans antialiased">
        <ThemeProvider attribute="class" defaultTheme="dark" enableSystem disableTransitionOnChange>
          <AppLayout>{children}</AppLayout>
        </ThemeProvider>
      </body>
    </html>
  )
}
