import { expect, test, type Page } from "@playwright/test"

const proposal = { id: "p1", kind: "duplicate", automatic: true, reason: "Identical content and metadata; originals remain recoverable.",
  memory_ids: ["a", "b"], sources: [{ id: "a", content: "Cache login sessions" }, { id: "b", content: "Cache login sessions" }] }
const cycle = { proposals: [proposal], scanned: 2, partial: false }

async function mockDreaming(page: Page) {
  let enabled = false
  let runs: any[] = []
  const requests: { method: string; path: string; body: any; contentType: string }[] = []
  await page.route("**/*", async (route) => {
    const req = route.request()
    const path = new URL(req.url()).pathname
    let body: unknown
    if (path === "/auth/status") body = { auth_enabled: false, setup_required: false }
    else if (path === "/auth/me") return route.fulfill({ status: 401, body: "{}" })
    else if (path === "/repos/scopes") body = [{ id: "repo-a", name: "Project A", registered: true }, { id: "repo-b", name: "Project B", registered: true }]
    else if (path === "/") body = { status: "online", storage_ready: true }
    else if (path.startsWith("/dreaming/")) {
      requests.push({ method: req.method(), path, body: req.postData() ? req.postDataJSON() : null, contentType: req.headers()["content-type"] || "" })
      if (path.endsWith("/preview")) body = cycle
      else if (path.endsWith("/run")) {
        const report = { ...cycle, id: "r1", created_at: "2026-09-07T10:00:00Z", proposals: [{ ...proposal, action_id: "action1", resolution: "applied" }] }
        runs = [report]; body = report
      } else if (path.endsWith("/schedule")) { enabled = req.postDataJSON().enabled; body = { enabled, interval_hours: 24 } }
      else if (path.endsWith("/undo")) { runs[0].proposals[0].resolution = "undone"; body = { status: "undone" } }
      else body = { settings: { enabled, interval_hours: 24 }, runs: path.includes("repo-b") ? [] : runs }
    } else return route.continue()
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(body) })
  })
  return requests
}

test("previews without writes, runs exact cleanup, and shows undo history", async ({ page }) => {
  const requests = await mockDreaming(page)
  await page.goto("/dashboard/dreaming?repo_id=repo-a")
  await expect(page.getByRole("heading", { name: "Dreaming", exact: true })).toBeVisible()
  await page.getByRole("button", { name: "Preview cycle", exact: true }).click()
  await expect(page.getByRole("heading", { name: "Cycle preview" })).toBeVisible()
  expect(requests.filter((request) => request.method !== "GET")).toEqual([])
  await page.getByText("Inspect 2 source memories").click()
  await expect(page.getByText("Cache login sessions", { exact: true })).toHaveCount(2)
  await page.getByRole("button", { name: "Run dreaming now" }).click()
  await expect(page.getByText("Applied · recoverable")).toBeVisible()
  await page.getByRole("button", { name: "Undo change" }).click()
  await expect(page.getByText("Undone", { exact: true })).toBeVisible()
})

test("enables and pauses the selected project's schedule with JSON requests", async ({ page }) => {
  const requests = await mockDreaming(page)
  await page.goto("/dashboard/dreaming?repo_id=repo-a")
  await page.getByRole("button", { name: "Enable dreaming" }).click()
  await expect(page.getByRole("heading", { name: "Scheduled dreaming is on" })).toBeVisible()
  const setting = requests.find((request) => request.method === "PUT")!
  expect(setting.path).toBe("/dreaming/repo-a/schedule")
  expect(setting.body).toEqual({ enabled: true, interval_hours: 24 })
  expect(setting.contentType).toContain("application/json")
  await page.getByRole("button", { name: "Pause", exact: true }).click()
  await expect(page.getByRole("heading", { name: "Scheduled dreaming is paused" })).toBeVisible()
})

test("rejects a late preview from a previously selected project", async ({ page }) => {
  await mockDreaming(page)
  let release: (() => void) | undefined
  const wait = new Promise<void>((resolve) => { release = resolve })
  await page.route((url) => url.pathname === "/dreaming/repo-a/preview", async (route) => {
    await wait
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(cycle) })
  })
  await page.goto("/dashboard/dreaming?repo_id=repo-a")
  await page.getByRole("button", { name: "Preview cycle", exact: true }).click()
  await page.getByRole("combobox", { name: /project/i }).selectOption("repo-b")
  release?.()
  await expect(page).toHaveURL(/repo_id=repo-b/)
  await expect(page.getByRole("heading", { name: "Cycle preview" })).toHaveCount(0)
  await expect(page.getByText("No cycles yet.", { exact: false })).toBeVisible()
})

test("reports an administrator restriction without showing other project data", async ({ page }) => {
  await mockDreaming(page)
  await page.route((url) => url.pathname.startsWith("/dreaming/"), (route) => route.fulfill({ status: 403, contentType: "application/json", body: JSON.stringify({ detail: "Dreaming controls require an administrator" }) }))
  await page.goto("/dashboard/dreaming?repo_id=repo-a")
  await expect(page.getByRole("alert").filter({ hasText: "administrator" })).toBeVisible()
  await expect(page.getByRole("button", { name: "Run dreaming now" })).toHaveCount(0)
})
