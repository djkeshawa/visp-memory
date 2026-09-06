import { expect, test, type Page } from "@playwright/test"

async function mockWorkspace(page: Page, options: { setup?: boolean } = {}) {
  let signedIn = !options.setup
  let setup = !!options.setup
  await page.route("**/*", async (route) => {
    const path = new URL(route.request().url()).pathname
    let body: unknown
    let status = 200
    if (path === "/auth/status") body = { auth_enabled: true, setup_required: setup }
    else if (path === "/auth/me") {
      status = signedIn ? 200 : 401
      body = signedIn ? { user: { id: "owner", username: "owner", role: "admin" } } : { detail: "Sign in" }
    } else if (path === "/auth/setup") { setup = false; body = { id: "owner", role: "admin" } }
    else if (path === "/auth/login") { signedIn = true; body = { user: { id: "owner", username: "owner", role: "admin" } } }
    else if (path === "/repos/scopes") body = [{ id: "repo-a", name: "Project A", registered: true }]
    else if (path === "/") body = { status: "online", storage_ready: true, embedding_driver_connected: false }
    else if (path === "/diagnostics/embedding-index") body = { status: "unavailable", message: "Keyword fallback is active" }
    else if (path === "/intents") body = []
    else return route.continue()
    await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) })
  })
}

test("creates first account using a private code then shows search setup", async ({ page }) => {
  await mockWorkspace(page, { setup: true })
  await page.goto("/dashboard/auth#setup=local-operator-one-use-code")
  await expect(page.getByRole("heading", { name: "Create administrator" })).toBeVisible()
  expect(new URL(page.url()).hash).toBe("")
  await page.getByLabel("Username", { exact: true }).fill("owner")
  await page.getByLabel("Password", { exact: true }).fill("long-local-password")
  await page.getByLabel("Confirm password").fill("long-local-password")
  const request = page.waitForRequest((req) => new URL(req.url()).pathname === "/auth/setup")
  await page.getByRole("button", { name: "Create account and continue", exact: true }).click()
  expect((await request).postDataJSON().setup_token).toBe("local-operator-one-use-code")
  await expect(page.getByRole("heading", { name: "Choose how to find your memories" })).toBeVisible()
  await expect(page.getByText("Keyword search is available", { exact: true })).toBeVisible()
  await page.getByRole("radio", { name: /^OpenAI/ }).check()
  await expect(page.getByRole("heading", { name: "Configure OpenAI" })).toBeVisible()
})

test("reflects external completion and shows evidence and history after refresh", async ({ page }) => {
  await mockWorkspace(page)
  let completed = false
  await page.route((url) => url.pathname === "/intents", async (route) => {
    const report = { source: "assistant", task_id: "task-1", revision: 1, status: "completed", summary: "Login delivered",
      evidence: [{ description: "Acceptance tests passed" }], checks: [{ description: "Login", status: "passed" }] }
    await route.fulfill({ contentType: "application/json", body: JSON.stringify([{
      id: "intent-1", description: "Deliver login", status: completed ? "completed" : "active", priority: 1,
      created_at: "2026-09-01T00:00:00Z", context: completed ? { external_workflow: report,
        workflow_history: [{ ...report, event_id: "done", recorded_at: "2026-09-07T00:00:00Z" }] } : {},
    }]) })
  })
  await page.goto("/dashboard/intents?repo_id=repo-a")
  await expect(page.getByText("Waiting for a completion report from your assistant or workflow.")).toBeVisible()
  completed = true
  await page.evaluate(() => window.dispatchEvent(new Event("focus")))
  await page.getByText("Reported completed by assistant", { exact: true }).click()
  await expect(page.getByText("Acceptance tests passed", { exact: true })).toBeVisible()
  await expect(page.getByText("Status history", { exact: true })).toBeVisible()
  await expect(page.getByText("Task: task-1 · Revision 1")).toBeVisible()
})

test("explains search matches and flags stale or conflicting knowledge", async ({ page }) => {
  await mockWorkspace(page)
  await page.route("**/recall", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify([{
    id: "memory-1", content: "Login uses sessions", layer: "semantic", importance: 0.7, tags: [],
    created_at: "2026-01-01T00:00:00Z", retrieval_method: "keyword", match_explanation: "Matched words: login",
    relevance_score: 0.75, quality_flags: ["possible_conflict"], valid_to: "2025-01-01T00:00:00Z",
    evidence_ids: ["evidence-1"], source: "assistant", metadata: {},
  }]) }))
  await page.goto("/dashboard/recall?repo_id=repo-a")
  await page.getByPlaceholder("What did I decide about authentication?").fill("login")
  await page.getByRole("button", { name: "Search", exact: true }).click()
  await expect(page.getByText("Keyword search", { exact: true })).toBeVisible()
  await expect(page.getByText("May be stale · review before using")).toBeVisible()
  await expect(page.getByText("Possible conflict · review sources")).toBeVisible()
  await page.getByText("Match explanation and quality", { exact: true }).click()
  await expect(page.getByText("Matched words: login")).toBeVisible()
  await expect(page.getByText("1 linked evidence records")).toBeVisible()
})
