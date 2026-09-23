import { expect, test, type Page, type Route } from "@playwright/test"

function memory(id: string) {
  return {
    id, content: `Project note ${id}`, layer: "semantic", category: "fact",
    repo_id: "repo-a", status: "active", created_at: "2026-01-01T00:00:00Z",
    tags: [], metadata: {},
  }
}

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) })
}

async function mockDashboard(page: Page) {
  await page.route("**/*", async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === "/auth/status") return json(route, { auth_enabled: false })
    if (path === "/auth/me") return json(route, { detail: "No account" }, 401)
    if (path === "/repos/scopes") return json(route, [
      { id: "repo-a", name: "Project A", registered: true },
      { id: "repo-b", name: "Project B", registered: true },
    ])
    if (path === "/") return json(route, { status: "online", repo_id: "repo-a", storage_ready: true })
    if (path === "/status") return json(route, { stats: {} })
    if (path === "/memories") return json(route, [memory("m1"), memory("m2"), memory("m3")])
    return route.continue()
  })
}

async function selectMergePair(page: Page) {
  await page.getByRole("checkbox", { name: "Select memory m1", exact: true }).check()
  await page.getByRole("checkbox", { name: "Select memory m2", exact: true }).check()
  await page.getByRole("button", { name: "Merge selected (2)", exact: true }).click()
}

test("requires a new preview when the merge selection changes", async ({ page }) => {
  await mockDashboard(page)
  let previewCount = 0
  let release = () => {}
  const pending = new Promise<void>((resolve) => { release = resolve })
  await page.route("**/memories/merge/preview", async (route) => {
    const input = route.request().postDataJSON()
    if (++previewCount === 2) await pending
    return json(route, {
      memory_ids: input.memory_ids, target_id: input.target_id, exact_duplicate: false,
      warnings: [input.memory_ids.includes("m3") ? "Review m1 and m3" : "Review m1 and m2"],
      validation_errors: [],
    })
  })
  await page.goto("/dashboard/memories?repo_id=repo-a")
  await selectMergePair(page)
  await expect(page.getByText("Review m1 and m2")).toBeVisible()
  await page.getByRole("button", { name: "Cancel", exact: true }).click()
  await page.getByRole("checkbox", { name: "Select memory m2", exact: true }).uncheck()
  await page.getByRole("checkbox", { name: "Select memory m3", exact: true }).check()
  await page.getByRole("button", { name: "Merge selected (2)", exact: true }).click()
  try {
    await expect(page.getByRole("button", { name: "Confirm reviewed merge", exact: true })).toBeDisabled()
    await expect(page.getByText("Review m1 and m2")).toHaveCount(0)
  } finally { release() }
  await expect(page.getByText("Review m1 and m3")).toBeVisible()
  await expect(page.getByRole("button", { name: "Confirm reviewed merge", exact: true })).toBeEnabled()
  await page.route("**/memories/merge", (route) => json(route, {
    ...route.request().postDataJSON(), operation_id: "merge-reviewed", exact_duplicate: false,
  }))
  const merged = page.waitForRequest((request) => new URL(request.url()).pathname === "/memories/merge")
  await page.getByRole("button", { name: "Confirm reviewed merge", exact: true }).click()
  expect((await merged).postDataJSON()).toEqual({ memory_ids: ["m1", "m3"], target_id: "m1", reviewed: true })
})

test("ignores an older canonical preview and prevents merging while the preview is pending", async ({ page }) => {
  await mockDashboard(page)
  let release = () => {}
  const pending = new Promise<void>((resolve) => { release = resolve })
  let delayed = false
  await page.route("**/memories/merge/preview", async (route) => {
    const input = route.request().postDataJSON()
    if (input.target_id === "m2") { delayed = true; await pending }
    return json(route, {
      memory_ids: input.memory_ids, target_id: input.target_id, exact_duplicate: false,
      warnings: [`Canonical: ${input.target_id}`], validation_errors: [],
    })
  })
  await page.goto("/dashboard/memories?repo_id=repo-a")
  await selectMergePair(page)
  await expect(page.getByText("Canonical: m1")).toBeVisible()
  await page.getByRole("combobox", { name: "Canonical memory", exact: true }).selectOption("m2")
  const staleResponse = page.waitForResponse((response) => new URL(response.url()).pathname === "/memories/merge/preview" && response.request().postDataJSON().target_id === "m2")
  try {
    await expect.poll(() => delayed).toBe(true)
    await expect(page.getByRole("button", { name: "Confirm reviewed merge", exact: true })).toBeDisabled()
    await page.getByRole("combobox", { name: "Canonical memory", exact: true }).selectOption("m1")
    await expect(page.getByText("Canonical: m1")).toBeVisible()
  } finally { release() }
  await (await staleResponse).finished()
  await expect(page.getByText("Canonical: m2")).toHaveCount(0)
  await expect(page.getByRole("button", { name: "Confirm reviewed merge", exact: true })).toBeEnabled()
})

test("keeps the current overview usable when an earlier project's memory save finishes", async ({ page }) => {
  await mockDashboard(page)
  let release = () => {}
  const pending = new Promise<void>((resolve) => { release = resolve })
  await page.route("**/memories", async (route) => {
    if (route.request().method() !== "POST") return route.fallback()
    await pending
    return json(route, memory("saved"))
  })
  await page.goto("/dashboard?repo_id=repo-a")
  await expect(page.getByLabel("Memory totals")).toHaveAttribute("aria-busy", "false")
  await page.getByRole("button", { name: "New memory", exact: true }).click()
  await page.getByLabel("Content", { exact: true }).fill("Save this note in project A")
  await page.getByRole("button", { name: "Save memory", exact: true }).click()
  await expect(page.getByRole("button", { name: "Saving...", exact: true })).toBeVisible()
  await page.keyboard.press("Escape")
  await page.getByRole("combobox", { name: "Project", exact: true }).selectOption("repo-b")
  await expect(page.getByLabel("Memory totals")).toHaveAttribute("aria-busy", "false")
  const saved = page.waitForResponse((response) => response.request().method() === "POST" && new URL(response.url()).pathname === "/memories")
  release()
  await saved
  await page.getByRole("button", { name: "New memory", exact: true }).click()
  await page.getByLabel("Content", { exact: true }).fill("Project B draft")
  await page.keyboard.press("Escape")
  await expect(page.getByLabel("Memory totals")).toHaveAttribute("aria-busy", "false")
  await expect(page.getByRole("button", { name: "Refresh dashboard" })).toBeEnabled()
})

test("ends a cold project's loading gate when the scope request never responds", async ({ page }) => {
  await mockDashboard(page)
  await page.clock.install()
  let release = () => {}
  const pending = new Promise<void>((resolve) => { release = resolve })
  await page.route("**/repos/scopes", async (route) => { await pending; return json(route, []) })
  await page.goto("/dashboard")
  await expect(page.getByText("Loading projects…")).toBeVisible()
  try {
    await page.clock.fastForward(12500)
    await expect(page.getByRole("heading", { name: "Memory overview" })).toBeVisible()
    await expect(page.getByRole("alert").filter({ hasText: /server/i })).toBeVisible()
  } finally { release() }
})

test("pages through older memories and resets paging when the project or status changes", async ({ page }) => {
  await mockDashboard(page)
  const records = Array.from({ length: 201 }, (_, index) => memory(`m${index + 1}`))
  const offsets: number[] = []
  await page.route((url) => url.pathname === "/memories", (route) => {
    const params = new URL(route.request().url()).searchParams
    const offset = Number(params.get("offset") || 0)
    offsets.push(offset)
    return json(route, records.slice(offset, offset + Number(params.get("limit"))))
  })
  await page.goto("/dashboard/memories?repo_id=repo-a")
  await expect(page.getByText("Project note m1", { exact: true })).toBeVisible()
  await expect(page.getByRole("button", { name: "Previous page" })).toBeDisabled()
  for (let pageNumber = 2; pageNumber <= 5; pageNumber++) {
    await page.getByRole("button", { name: "Next page" }).click()
    await expect(page.getByText(`Project note m${(pageNumber - 1) * 50 + 1}`, { exact: true })).toBeVisible()
  }
  await expect(page.getByRole("button", { name: "Next page" })).toBeDisabled()
  expect([...new Set(offsets)]).toEqual([0, 50, 100, 150, 200])
  await page.getByRole("combobox", { name: "Project", exact: true }).selectOption("repo-b")
  await expect(page.getByText("Project note m1", { exact: true })).toBeVisible()
  await expect(page.getByRole("button", { name: "Previous page" })).toBeDisabled()
  await page.getByRole("combobox", { name: "Project", exact: true }).selectOption("repo-a")
  await expect(page.getByText("Project note m1", { exact: true })).toBeVisible()
  await expect(page.getByRole("button", { name: "Previous page" })).toBeDisabled()
  await page.getByRole("button", { name: "Next page" }).click()
  await expect(page.getByText("Project note m51", { exact: true })).toBeVisible()
  await page.getByRole("tab", { name: "deleted", exact: true }).click()
  await expect(page.getByText("Project note m1", { exact: true })).toBeVisible()
  await expect(page.getByRole("button", { name: "Previous page" })).toBeDisabled()
})

test("returns to the previous page after deleting the final memory on the last page", async ({ page }) => {
  await mockDashboard(page)
  let records = Array.from({ length: 51 }, (_, index) => memory(`m${index + 1}`))
  await page.route((url) => url.pathname === "/memories", (route) => {
    const params = new URL(route.request().url()).searchParams
    const offset = Number(params.get("offset") || 0)
    return json(route, records.slice(offset, offset + Number(params.get("limit"))))
  })
  await page.route("**/memories/m51?*", (route) => {
    records = records.filter((record) => record.id !== "m51")
    return json(route, { id: "m51", status: "deleted" })
  })
  await page.goto("/dashboard/memories?repo_id=repo-a")
  await page.getByRole("checkbox", { name: "Select memory m1", exact: true }).check()
  await page.getByRole("button", { name: "Next page" }).click()
  await expect(page.getByText("Project note m51", { exact: true })).toBeVisible()
  await expect(page.getByRole("button", { name: "Merge selected (0)", exact: true })).toBeDisabled()
  await page.getByTitle("Move to trash", { exact: true }).click()
  await expect(page.getByText("Project note m1", { exact: true })).toBeVisible()
  await expect(page.getByRole("button", { name: "Previous page" })).toBeDisabled()
  await expect(page.getByRole("button", { name: "Next page" })).toBeDisabled()
  await expect(page.getByRole("checkbox", { name: "Select memory m1", exact: true })).not.toBeChecked()
})
