import { expect, test, type Page, type Route } from "@playwright/test"

const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) })

const repo = (id: string, name: string, status = "active") => ({
  id, name, description: `${name} scope`, tech_stack: [], status, created_at: "2026-01-01T00:00:00Z",
})

/** Mocks the shell endpoints every page needs, then lets `handler` claim page-specific ones. */
async function mockApi(page: Page, handler: (url: URL, method: string, route: Route) => Promise<boolean> | boolean) {
  await page.route("**/*", async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (await handler(url, request.method(), route)) return
    if (url.pathname === "/auth/status") return json(route, { auth_enabled: false, setup_required: false })
    if (url.pathname === "/auth/me") return json(route, { detail: "Authentication required" }, 401)
    if (url.pathname === "/repos/scopes") return json(route, [{ id: "repo-a", name: "Project A", registered: true, status: "active" }])
    if (url.pathname === "/") return json(route, { status: "online", repo_id: "repo-a", storage_ready: true, auth_enabled: false })
    if (url.pathname === "/status") return json(route, { stats: { total_memories: 0, active_intents: 0, memories_by_layer: {}, total_relationships: 0 } })
    return route.continue()
  })
}

test("purge requires the typed project id and sends the confirmation", async ({ page }) => {
  let projects = [repo("repo-a", "Project A"), repo("repo-old", "Old project", "archived")]
  let purgeUrl: URL | null = null
  await mockApi(page, async (url, method, route) => {
    if (url.pathname === "/repos" && method === "GET") { await json(route, projects); return true }
    if (url.pathname === "/repos/repo-old/purge-preview") { await json(route, { memories: 7, intents: 2, relationships: 3, content_bytes: 10 }); return true }
    if (url.pathname === "/repos/repo-old" && method === "DELETE") {
      purgeUrl = url
      projects = [projects[0]]
      await json(route, { status: "purged", id: "repo-old", backup: "b.json" })
      return true
    }
    return false
  })
  await page.goto("/dashboard/projects?repo_id=repo-a")
  await page.getByLabel("Show archived").check()

  const purgeButton = page.getByRole("button", { name: /^Purge/ })
  await expect(purgeButton).toBeDisabled()
  await page.getByLabel("Purge target").selectOption("repo-old")
  await expect(purgeButton).toHaveText("Purge repo-old…")
  await purgeButton.click()

  const dialog = page.getByRole("dialog")
  await expect(dialog.getByText("Permanently purge project")).toBeVisible()
  await expect(dialog.getByText("Memories").locator("xpath=following-sibling::dd")).toHaveText("7")
  const confirm = dialog.getByRole("button", { name: "Purge project" })
  await expect(confirm).toBeDisabled()
  await dialog.getByLabel(/Type .* to confirm/).fill("repo-ol")
  await expect(confirm).toBeDisabled()
  await dialog.getByLabel(/Type .* to confirm/).fill("repo-old")
  await expect(confirm).toBeEnabled()
  await confirm.click()

  await expect(dialog).toBeHidden()
  expect(purgeUrl!.searchParams.get("confirmation")).toBe("repo-old")
  await expect(page.getByRole("row").filter({ hasText: "Old project" })).toHaveCount(0)
})

test("creates a token, shows the secret once, and revokes it", async ({ page }) => {
  const token = { id: "tok-1", name: "Codex", token_prefix: "vmt_abc", scopes: ["memory:read"], repo_ids: [], created_at: "2026-01-01T00:00:00Z", last_used_at: null, revoked_at: null }
  let tokens: unknown[] = []
  let createBody: Record<string, unknown> | null = null
  let revoked = false
  await mockApi(page, async (url, method, route) => {
    if (url.pathname === "/auth/tokens" && method === "GET") { await json(route, tokens); return true }
    if (url.pathname === "/auth/tokens" && method === "POST") {
      createBody = JSON.parse(route.request().postData() || "{}")
      tokens = [token]
      await json(route, { ...token, token: "vmt_secret_value" })
      return true
    }
    if (url.pathname === "/auth/tokens/tok-1" && method === "DELETE") { revoked = true; tokens = []; await route.fulfill({ status: 204 }); return true }
    return false
  })
  await page.goto("/dashboard/integrations?repo_id=repo-a")

  await page.getByRole("button", { name: "Create token" }).click()
  await expect(page.getByText("Give the token a name and select at least one permission.")).toBeVisible()

  await page.getByLabel("Token name").fill("Codex")
  await page.getByRole("button", { name: "Create token" }).click()
  await expect(page.getByText("vmt_secret_value")).toBeVisible()
  await expect(page.getByText(/shown once/)).toBeVisible()
  expect(createBody).toMatchObject({ name: "Codex", repo_ids: [] })
  expect((createBody as unknown as { scopes: string[] }).scopes).toContain("memory:read")

  await page.getByRole("button", { name: "Done" }).click()
  await expect(page.getByText("vmt_secret_value")).toHaveCount(0)

  await expect(page.getByRole("row").filter({ hasText: "Codex" })).toBeVisible()
  await page.getByRole("button", { name: "Revoke Codex" }).click()
  await expect(page.getByText("No active integration tokens.")).toBeVisible()
  expect(revoked).toBe(true)
})

test("creates an account and toggles an account's access", async ({ page }) => {
  let users: Record<string, unknown>[] = [{ id: "u1", username: "owner", display_name: "Owner", role: "admin", enabled: true, last_login_at: null }]
  let createBody: Record<string, unknown> | null = null
  let patchBody: Record<string, unknown> | null = null
  await mockApi(page, async (url, method, route) => {
    if (url.pathname === "/auth/users" && method === "GET") { await json(route, users); return true }
    if (url.pathname === "/auth/users" && method === "POST") {
      createBody = JSON.parse(route.request().postData() || "{}")
      users = [...users, { id: "u2", username: "sam", display_name: null, role: "user", enabled: true, last_login_at: null }]
      await json(route, users[1])
      return true
    }
    if (url.pathname === "/auth/users/u2" && method === "PATCH") {
      patchBody = JSON.parse(route.request().postData() || "{}")
      users = [users[0], { ...users[1], enabled: false }]
      await json(route, users[1])
      return true
    }
    return false
  })
  await page.goto("/dashboard/users?repo_id=repo-a")
  await expect(page.getByRole("heading", { name: "Accounts · 1" })).toBeVisible()

  await page.getByLabel("Username").fill("sam")
  await page.getByLabel("Temporary password").fill("a-long-password-1")
  await page.getByRole("button", { name: "Create account" }).click()
  await expect(page.getByRole("heading", { name: "Accounts · 2" })).toBeVisible()
  expect(createBody).toMatchObject({ username: "sam", role: "user" })

  await page.getByRole("button", { name: "Disable sam" }).click()
  await expect(page.getByRole("button", { name: "Enable sam" })).toBeVisible()
  expect(patchBody).toEqual({ enabled: false })
})

const candidate = {
  memory_id: "mem-1", snippet: "Old fact", layer: "semantic", category: "note", repo_id: "repo-a",
  current_importance: 0.6, projected_importance: 0.3, decay_amount: 0.3, age_days: 40, access_count: 2,
  risk: "weakening", reason: "Idle for 40 days",
}

test("operations shows the decay preview and re-runs it with new inputs", async ({ page }) => {
  const requests: URL[] = []
  await mockApi(page, async (url, _method, route) => {
    if (url.pathname === "/quality/decay-preview") {
      requests.push(url)
      await json(route, { halflife_days: 30, min_importance: 0.1, decay_enabled: true, candidates: [candidate] })
      return true
    }
    return false
  })
  await page.goto("/dashboard/health?repo_id=repo-a")
  await expect(page.getByText("Old fact")).toBeVisible()
  await expect(page.getByText("60% → 30%")).toBeVisible()
  await expect(page.getByText("Decay enabled")).toBeVisible()
  expect(requests[0].searchParams.get("repo_id")).toBe("repo-a")

  const before = requests.length
  await page.getByLabel("Half-life days").fill("60")
  await page.getByRole("button", { name: "Preview" }).click()
  await expect.poll(() => requests.length).toBe(before + 1)
  expect(requests[before].searchParams.get("halflife_days")).toBe("60")
})

test("operations surfaces a decay preview failure and keeps the page usable", async ({ page }) => {
  await mockApi(page, async (url, _method, route) => {
    if (url.pathname === "/quality/decay-preview") { await json(route, { detail: "preview exploded" }, 500); return true }
    return false
  })
  await page.goto("/dashboard/health?repo_id=repo-a")
  await expect(page.locator("p[role=alert]")).toBeVisible()
  await expect(page.getByRole("button", { name: "Preview" })).toBeEnabled()
})
