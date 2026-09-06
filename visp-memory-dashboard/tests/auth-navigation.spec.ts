import { expect, test, type Page } from "@playwright/test"

const account = { user: { id: "user-1", username: "reader", role: "user", enabled: true } }

async function mockAuthentication(page: Page, options: { signedIn?: boolean; disabled?: boolean } = {}) {
  let signedIn = options.signedIn ?? false
  const protectedRequests: string[] = []
  await page.route("**/*", async (route) => {
    const path = new URL(route.request().url()).pathname
    let body: unknown
    let status = 200
    if (path === "/auth/status") body = { auth_enabled: !options.disabled, setup_required: false }
    else if (path === "/auth/me") {
      status = signedIn ? 200 : 401
      body = signedIn ? account : { detail: "Authentication required" }
    } else if (path === "/auth/login") {
      signedIn = true
      body = account
    } else if (path === "/repos/scopes") {
      protectedRequests.push(path)
      body = [{ id: "repo-a", name: "Project A", registered: true }]
    } else if (path === "/") body = { status: "online", storage_ready: true }
    else if (path === "/status") { protectedRequests.push(path); body = { stats: {} } }
    else if (path === "/memories") { protectedRequests.push(path); body = [] }
    else return route.continue()
    await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) })
  })
  return protectedRequests
}

test("opens login before requesting or showing protected dashboard content", async ({ page }) => {
  const requests = await mockAuthentication(page)
  await page.addInitScript(() => sessionStorage.setItem("visp-memory-auth-user", '{"id":"stale"}'))
  await page.goto("/dashboard?repo_id=repo-a")
  await expect(page).toHaveURL(/\/dashboard\/auth\?next=/)
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible()
  await expect(page.getByRole("heading", { name: "Memory overview" })).toHaveCount(0)
  await expect(page.getByRole("navigation")).toHaveCount(0)
  expect(requests).toEqual([])
})

test("returns to the requested project page after signing in", async ({ page }) => {
  await mockAuthentication(page)
  await page.goto("/dashboard/memories?repo_id=repo-a")
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible()
  await page.getByLabel("Username", { exact: true }).fill("reader")
  await page.getByLabel("Password", { exact: true }).fill("test-password")
  await page.getByRole("button", { name: "Sign in", exact: true }).click()
  await expect(page).toHaveURL(/\/dashboard\/memories\?repo_id=repo-a$/)
  await expect(page.getByRole("navigation", { name: "Primary navigation" })).toBeVisible()
})

test("keeps the dashboard hidden until the server validates the session", async ({ page }) => {
  const requests = await mockAuthentication(page, { signedIn: true })
  let release: (() => void) | undefined
  const pending = new Promise<void>((resolve) => { release = resolve })
  await page.route("**/auth/me", async (route) => {
    await pending
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(account) })
  })
  await page.goto("/dashboard")
  await expect(page.getByText("Checking your session…")).toBeVisible()
  await expect(page.getByRole("heading", { name: "Memory overview" })).toHaveCount(0)
  expect(requests).toEqual([])
  release?.()
  await expect(page.getByRole("heading", { name: "Memory overview" })).toBeVisible()
})

test("allows explicitly anonymous servers without redirecting to login", async ({ page }) => {
  await mockAuthentication(page, { disabled: true })
  await page.goto("/dashboard")
  await expect(page.getByRole("heading", { name: "Memory overview" })).toBeVisible()
})

test("offers retry without exposing dashboard content when auth checks fail", async ({ page }) => {
  const requests = await mockAuthentication(page, { signedIn: true })
  await page.route("**/auth/status", (route) => route.fulfill({ status: 503, body: "Unavailable" }))
  await page.goto("/dashboard")
  await expect(page.getByRole("heading", { name: "Unable to check your session" })).toBeVisible()
  expect(requests).toEqual([])
  await page.unroute("**/auth/status")
  await page.getByRole("button", { name: "Try again" }).click()
  await expect(page.getByRole("heading", { name: "Memory overview" })).toBeVisible()
})

test("does not follow an external return URL after authentication", async ({ page }) => {
  await mockAuthentication(page, { signedIn: true })
  await page.goto("/dashboard/auth?next=https%3A%2F%2Fexample.com%2Fdashboard")
  await expect(page).toHaveURL(/\/dashboard\?repo_id=repo-a$/)
})

test("returns an expired session to login after a protected request is rejected", async ({ page }) => {
  await mockAuthentication(page, { signedIn: true })
  await page.goto("/dashboard")
  await expect(page.getByRole("heading", { name: "Memory overview" })).toBeVisible()
  await page.route("**/auth/me", (route) => route.fulfill({ status: 401, body: '{"detail":"Session expired"}' }))
  await page.route("**/status?*", (route) => route.fulfill({ status: 401, body: '{"detail":"Session expired"}' }))
  await page.getByRole("button", { name: "Refresh dashboard" }).click()
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible()
  await expect(page.getByRole("navigation")).toHaveCount(0)
})

test("does not treat a malformed authentication status as anonymous access", async ({ page }) => {
  const requests = await mockAuthentication(page)
  await page.route("**/auth/status", (route) => route.fulfill({ contentType: "application/json", body: "{}" }))
  await page.goto("/dashboard")
  await expect(page.getByRole("heading", { name: "Unable to check your session" })).toBeVisible()
  expect(requests).toEqual([])
})
