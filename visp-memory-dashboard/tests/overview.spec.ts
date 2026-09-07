import { expect, test, type Page } from "@playwright/test"

const memories = [
  { id: "decision", content: "Keep project recall scoped to the active repository. Shared context must be explicitly requested so unrelated project knowledge stays separate.", layer: "episodic", category: "decision", tags: ["retrieval", "project scope"] },
  { id: "knowledge", content: "Memory supplies cited knowledge. Permission and readiness decisions belong to the host's policy engine.", layer: "semantic", category: "architecture", tags: ["boundaries"] },
  { id: "intent", content: "Review the onboarding flow with a fresh workspace before the next release.", layer: "intent", category: "planning", tags: [] },
  { id: "lesson", content: "Keyword search remains available when embeddings are not configured. A local install should still provide useful recall.", layer: "episodic", category: "lesson", tags: ["local setup"] },
]

async function mockOverview(page: Page, options: { failed?: boolean; empty?: boolean } = {}) {
  let records = options.empty ? [] : memories
  await page.route("**/*", async (route) => {
    const path = new URL(route.request().url()).pathname
    let body: unknown
    let status = 200
    if (path === "/auth/status") return route.fulfill({ contentType: "application/json", body: JSON.stringify({ auth_enabled: false, setup_required: false }) })
    if (path === "/repos/scopes") body = [{ id: "visp-memory", name: "Visp Memory", registered: true, status: "active" }]
    else if (path === "/auth/me") { body = { detail: "Authentication required" }; status = 401 }
    else if (path === "/") body = { status: "online", storage_ready: true, embedding_driver_status: "fallback", embedding_driver_connected: false }
    else if (path === "/status") {
      status = options.failed ? 503 : 200
      body = options.failed ? { detail: "Storage is unavailable" } : { stats: { total_memories: records.length, active_intents: 1, memories_by_layer: { semantic: 1 }, total_relationships: 12 } }
    } else if (path === "/memories") {
      if (route.request().method() === "POST") {
        const data = route.request().postDataJSON()
        records = [{ id: "new", content: data.content, layer: "episodic", category: data.category, tags: [] }, ...records]
        body = { ...records[0], created_at: "2026-09-06T09:00:00Z" }
      } else body = records.map((record) => ({ ...record, repo_id: "visp-memory", created_at: "2026-09-05T10:00:00Z" }))
    } else return route.continue()
    await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) })
  })
}

test("filters and expands recent memories and preserves project context in navigation", async ({ page }) => {
  await mockOverview(page)
  await page.goto("/dashboard?repo_id=visp-memory")
  await expect(page.getByRole("heading", { name: "Memory overview" })).toBeVisible()
  await expect(page.getByLabel("Memory totals")).toContainText("12")
  await page.getByRole("button", { name: "Semantic", exact: true }).click()
  await expect(page.locator("details")).toHaveCount(1)
  await expect(page.locator("summary")).toContainText("Memory supplies cited knowledge")
  await page.getByRole("button", { name: "All layers" }).click()
  await page.locator("summary").first().focus()
  await page.keyboard.press("Enter")
  await expect(page.locator("details").first()).toHaveAttribute("open", "")
  await expect(page.getByText("project scope", { exact: true })).toBeVisible()
  await expect(page.getByRole("link", { name: /Recall a memory/ })).toHaveAttribute("href", "/dashboard/recall?repo_id=visp-memory")
  await expect(page.getByRole("link", { name: "Overview", exact: true })).toHaveAttribute("aria-current", "page")
  await expect(page.getByText("Keyword fallback")).toBeVisible()
})

test("creates a memory with the keyboard and refreshes the overview", async ({ page }) => {
  await mockOverview(page)
  await page.goto("/dashboard?repo_id=visp-memory")
  await page.getByRole("button", { name: "New memory", exact: true }).focus()
  await page.keyboard.press("Enter")
  await expect(page.getByRole("dialog")).toBeVisible()
  await expect(page.getByRole("button", { name: "Save memory" })).toBeDisabled()
  await page.getByLabel("Content", { exact: true }).fill("Use explicit repository scope for recall.")
  await page.getByRole("button", { name: "Save memory" }).click()
  await expect(page.getByRole("dialog")).not.toBeVisible()
  await expect(page.locator("summary").first()).toContainText("Use explicit repository scope")
})

test("distinguishes unavailable data from an empty library and supports retry", async ({ page }) => {
  await mockOverview(page, { failed: true })
  await page.goto("/dashboard?repo_id=visp-memory")
  await expect(page.getByRole("alert").filter({ hasText: "Dashboard is not connected" })).toContainText("Dashboard is not connected")
  await expect(page.getByLabel("Memory totals")).toContainText("Unavailable")
  await expect(page.getByText("Start with something worth remembering")).toHaveCount(0)
  await page.route("**/status?*", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ stats: { total_memories: 4 } }) }))
  await page.getByRole("button", { name: "Retry connection" }).click()
  await expect(page.getByRole("alert").filter({ hasText: "Dashboard is not connected" })).toHaveCount(0)
  await expect(page.locator("details")).toHaveCount(4)
})

test("keeps an empty overview usable on mobile and supports dark mode", async ({ page }) => {
  await mockOverview(page, { empty: true })
  await page.setViewportSize({ width: 390, height: 844 })
  await page.emulateMedia({ colorScheme: "dark", reducedMotion: "reduce" })
  await page.goto("/dashboard?repo_id=visp-memory")
  await expect(page.getByText("Start with something worth remembering")).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await page.getByRole("button", { name: "Open navigation menu" }).click()
  await expect(page.getByRole("navigation", { name: "Mobile navigation" })).toBeVisible()
  const close = page.getByRole("dialog", { name: "Navigation menu" }).getByRole("button", { name: "Close navigation menu" })
  await close.focus()
  await page.keyboard.press("Shift+Tab")
  await expect(close).not.toBeFocused()
  await page.keyboard.press("Tab")
  await expect(close).toBeFocused()
  await page.keyboard.press("Escape")
  await expect(page.getByRole("dialog", { name: "Navigation menu" })).toHaveCount(0)
  await expect(page.getByRole("button", { name: "Open navigation menu" })).toBeFocused()
})

for (const view of [
  { name: "desktop", width: 1440, height: 1120, theme: "light" },
  { name: "dark", width: 1440, height: 1120, theme: "dark" },
  { name: "mobile", width: 390, height: 844, theme: "light" },
] as const) {
  test(`keeps populated content within the ${view.name} viewport`, async ({ page }, testInfo) => {
    await mockOverview(page)
    await page.setViewportSize(view)
    await page.addInitScript((theme) => localStorage.setItem("theme", theme), view.theme)
    await page.emulateMedia({ colorScheme: view.theme, reducedMotion: "reduce" })
    await page.goto("/dashboard?repo_id=visp-memory")
    await expect(page.locator("details")).toHaveCount(4)
    await expect(page.getByText("Keyword fallback")).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    await page.screenshot({ path: testInfo.outputPath(`${view.name}.png`), fullPage: true, animations: "disabled", style: "nextjs-portal { visibility: hidden; }" })
  })
}

test("does not report ready services before the connection check finishes", async ({ page }) => {
  await mockOverview(page)
  let release: (() => void) | undefined
  const pending = new Promise<void>((resolve) => { release = resolve })
  await page.route("**/", async (route) => {
    if (new URL(route.request().url()).pathname !== "/") return route.fallback()
    await pending
    await route.fulfill({ contentType: "application/json", body: JSON.stringify({ status: "online", storage_ready: true }) })
  })
  await page.goto("/dashboard?repo_id=visp-memory")
  const connection = page.getByRole("region", { name: "Connection status" })
  await expect(connection).toContainText("Checking…")
  await expect(connection).not.toContainText("Ready")
  release?.()
  await expect(connection).toContainText("Ready")
})

test("switches away from the resolved system theme on the first click", async ({ page }) => {
  await mockOverview(page)
  await page.addInitScript(() => localStorage.setItem("theme", "system"))
  await page.emulateMedia({ colorScheme: "dark" })
  await page.goto("/dashboard?repo_id=visp-memory")
  await expect(page.locator("html")).toHaveClass(/dark/)
  await page.getByRole("button", { name: "Switch to light mode" }).click()
  await expect(page.locator("html")).not.toHaveClass(/dark/)
  await page.getByRole("button", { name: "Switch to dark mode" }).click()
  await expect(page.locator("html")).toHaveClass(/dark/)
})

test("keeps recall usable on a narrow phone", async ({ page }) => {
  await mockOverview(page)
  await page.route("**/recall", async (route) => {
    if (route.request().method() !== "POST") return route.fallback()
    expect(route.request().postDataJSON().query).toBe("project decisions")
    await route.fulfill({ contentType: "application/json", body: "[]" })
  })
  await page.setViewportSize({ width: 320, height: 740 })
  await page.goto("/dashboard/recall?repo_id=visp-memory")
  await page.getByRole("searchbox", { name: "Search memories" }).fill("project decisions")
  await page.getByRole("button", { name: "Search", exact: true }).click()
  await expect(page.getByRole("button", { name: "Search", exact: true })).toBeEnabled()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
})


test("starts in dark mode and preserves an explicit light preference", async ({ page }) => {
  await mockOverview(page)
  await page.emulateMedia({ colorScheme: "light" })
  await page.goto("/dashboard?repo_id=visp-memory")
  await expect(page.locator("html")).toHaveClass(/dark/)
  await page.getByRole("button", { name: "Switch to light mode" }).click()
  await page.reload()
  await expect(page.locator("html")).toHaveClass(/light/)
  await expect(page.getByRole("button", { name: "Switch to dark mode" })).toBeVisible()
})
