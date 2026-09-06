import { expect, test, type Page, type Route } from "@playwright/test"

type Scope = { id: string; name: string; registered?: boolean; status?: "active" | "archived" }
type GraphNode = { id: string; label: string; layer: "raw" | "episodic" | "semantic" | "intent" }

const projectA: Scope = { id: "repo-a", name: "Project A", registered: true, status: "active" }
const projectB: Scope = { id: "repo-b", name: "Project B", registered: true, status: "active" }

async function json(route: Route, body: unknown, status = 200): Promise<true> {
  await route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  })
  return true
}

function memory(id: string, content: string, layer: GraphNode["layer"], repoId = "repo-a") {
  return {
    id,
    content,
    layer,
    category: "test",
    repo_id: repoId,
    status: "active",
    created_at: "2026-01-01T00:00:00Z",
    accessed_at: "2026-01-01T00:00:00Z",
    importance: 0.5,
    tags: [],
    metadata: {},
  }
}

async function installApiMocks(
  page: Page,
  options: {
    scopes?: Scope[]
    graph?: Record<string, { nodes: GraphNode[]; links?: unknown[] }>
    intents?: unknown[]
    projects?: unknown[]
    onRequest?: (url: URL, route: Route) => Promise<boolean | void>
  } = {},
) {
  const scopes = options.scopes || [projectA]
  const graph = options.graph || {
    "repo-a": { nodes: [], links: [] },
  }
  const intents = options.intents || []
  const projects = options.projects || scopes.map((scope) => ({
    id: scope.id,
    name: scope.name,
    description: "Test project",
    tech_stack: [],
    status: scope.status || "active",
    created_at: "2026-01-01T00:00:00Z",
  }))

  await page.route("**/*", async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const handled = await options.onRequest?.(url, route)
    if (handled) return

    if (url.pathname === "/auth/status") return json(route, { auth_enabled: false, setup_required: false })
    if (url.pathname === "/repos/scopes") return json(route, scopes)
    if (url.pathname === "/auth/me") return json(route, { detail: "Authentication required" }, 401)
    if (url.pathname === "/") {
      return json(route, {
        status: "online",
        repo_id: scopes[0]?.id || null,
        storage_ready: true,
        auth_enabled: false,
      })
    }
    if (url.pathname === "/status") {
      return json(route, {
        stats: {
          total_memories: 0,
          active_intents: intents.length,
          memories_by_layer: { semantic: 0 },
          total_relationships: 0,
        },
      })
    }
    if (url.pathname === "/graph") {
      return json(route, graph[url.searchParams.get("repo_id") || "repo-a"] || { nodes: [], links: [] })
    }
    if (url.pathname === "/intents" && request.method() === "GET") return json(route, intents)
    if (url.pathname === "/intents" && request.method() === "POST") {
      return json(route, {
        id: "intent-new",
        description: "New intent",
        priority: 1,
        status: "active",
        created_at: "2026-01-01T00:00:00Z",
        updated_at: "2026-01-01T00:00:00Z",
        context: {},
      })
    }
    if (url.pathname.match(/^\/intents\/[^/]+\/(complete|close|reopen)$/)) {
      return json(route, {
        id: url.pathname.split("/")[2],
        status: "active",
        authoritative: false,
        status_changed: false,
        outcome_recorded: true,
      })
    }
    if (url.pathname === "/repos" && request.method() === "GET") return json(route, projects)
    if (url.pathname === "/repos" && request.method() === "POST") return json(route, projects[0])
    if (url.pathname.match(/^\/repos\/[^/]+\/(archive|restore)$/)) {
      return json(route, { id: url.pathname.split("/")[2], status: "active" })
    }
    if (url.pathname.match(/^\/repos\/[^/]+\/purge-preview$/)) {
      return json(route, { memories: 0, intents: 0, relationships: 0, content_bytes: 0 })
    }
    if (url.pathname.match(/^\/repos\/[^/]+$/) && request.method() === "DELETE") {
      return json(route, { status: "purged", id: url.pathname.split("/")[2], backup: "backup.json" })
    }
    if (url.pathname === "/recall" && request.method() === "POST") {
      return json(route, [
        memory("raw-1", "secret raw capture", "raw"),
        memory("episode-1", "visible episodic memory", "episodic"),
      ])
    }
    if (url.pathname === "/memories") return json(route, [])
    return route.continue()
  })
}

test("shows raw as a styled opt-in graph layer but keeps it out of recall", async ({ page }) => {
  await installApiMocks(page, {
    graph: {
      "repo-a": {
        nodes: [
          { id: "raw-1", label: "raw node", layer: "raw" },
          { id: "episode-1", label: "episode node", layer: "episodic" },
          { id: "semantic-1", label: "semantic node", layer: "semantic" },
          { id: "intent-1", label: "intent node", layer: "intent" },
        ],
        links: [],
      },
    },
  })
  await page.goto("/dashboard/graph?repo_id=repo-a")
  await expect(page.getByTestId("graph-settlement")).toContainText("3 of 4 nodes visible")
  await page.getByRole("button", { name: "Show graph filters" }).click()
  const rawFilter = page.getByTestId("graph-filter-raw")
  await expect(rawFilter).toHaveText("Raw")
  await expect(rawFilter).toHaveAttribute("aria-pressed", "false")
  await rawFilter.click()
  await expect(page.getByTestId("graph-settlement")).toContainText("4 of 4 nodes visible")

  await page.goto("/dashboard/recall?repo_id=repo-a")
  await page.getByPlaceholder("What did I decide about authentication?").fill("memory")
  await page.getByRole("button", { name: "Search" }).click()
  await expect(page.getByText("visible episodic memory")).toBeVisible()
  await expect(page.getByText("secret raw capture")).toHaveCount(0)
})

test("labels complete and close actions as advisory outcomes and leaves active intents active", async ({ page }) => {
  await installApiMocks(page, {
    intents: [{
      id: "intent-1",
      description: "Ship the dashboard",
      priority: 2,
      status: "active",
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
      context: {},
    }],
  })
  await page.goto("/dashboard/intents?repo_id=repo-a")
  await expect(page.getByRole("button", { name: "Record completion outcome" })).toBeVisible()
  await expect(page.getByRole("button", { name: "Record close outcome" })).toBeVisible()
  await page.getByRole("button", { name: "Record completion outcome" }).click()
  await expect(page.getByRole("status")).toContainText("Completion outcome recorded")
  await expect(page.getByText("Ship the dashboard")).toBeVisible()
  await expect(page.getByText(/authoritative intent remains active/)).toBeVisible()
})

test("reports the returned status for close and reopen outcomes", async ({ page }) => {
  let currentStatus: "active" | "closed" = "active"
  const intent = {
    id: "intent-1",
    description: "Ship the dashboard",
    priority: 2,
    status: "active",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    context: {},
  }

  await installApiMocks(page, {
    intents: [intent],
    onRequest: async (url, route) => {
      if (url.pathname === "/intents" && route.request().method() === "GET") {
        return json(route, [{ ...intent, status: currentStatus }])
      }
      if (url.pathname === "/intents/intent-1/close") {
        currentStatus = "closed"
        return json(route, {
          id: "intent-1",
          status: "closed",
          authoritative: true,
          status_changed: true,
          outcome_recorded: true,
        })
      }
      if (url.pathname === "/intents/intent-1/reopen") {
        return json(route, {
          id: "intent-1",
          status: "closed",
          authoritative: true,
          status_changed: false,
          outcome_recorded: true,
        })
      }
      return false
    },
  })

  await page.goto("/dashboard/intents?repo_id=repo-a")
  await page.getByRole("button", { name: "Record close outcome" }).click()
  await expect(page.getByRole("status")).toContainText("authoritative status to closed")
  await expect(page.getByTitle("Reopen intent")).toBeVisible()

  await page.getByTitle("Reopen intent").click()
  await expect(page.getByRole("status")).toContainText("authoritative intent remains closed")
  await expect(page.getByTitle("Reopen intent")).toBeVisible()
})

test("exercises restore and permanent purge memory actions", async ({ page }) => {
  let deletedVisible = true
  let archivedVisible = true
  let restoreCalls = 0
  let purgeCalls = 0
  const deletedMemory = memory("deleted-1", "deleted memory", "episodic")
  const archivedMemory = memory("archived-1", "archived memory", "semantic")

  await installApiMocks(page, {
    onRequest: async (url, route) => {
      const request = route.request()
      if (url.pathname === "/memories" && request.method() === "GET") {
        const requestedStatus = url.searchParams.get("status")
        if (requestedStatus === "deleted") return json(route, deletedVisible ? [deletedMemory] : [])
        if (requestedStatus === "archived") return json(route, archivedVisible ? [archivedMemory] : [])
        return json(route, [])
      }
      if (url.pathname === "/memories/deleted-1/restore" && request.method() === "POST") {
        restoreCalls += 1
        deletedVisible = false
        return json(route, { status: "restored" })
      }
      if (url.pathname === "/memories/archived-1/purge" && request.method() === "DELETE") {
        purgeCalls += 1
        archivedVisible = false
        return json(route, { status: "purged" })
      }
      return false
    },
  })

  await page.goto("/dashboard/memories?repo_id=repo-a")
  await page.getByRole("tab", { name: "deleted" }).click()
  await expect(page.getByText("deleted memory")).toBeVisible()
  await page.getByTitle("Restore").click()
  await expect.poll(() => restoreCalls).toBe(1)
  await expect(page.getByText("deleted memory")).toHaveCount(0)

  await page.getByRole("tab", { name: "archived" }).click()
  await expect(page.getByText("archived memory")).toBeVisible()
  page.once("dialog", (dialog) => dialog.accept())
  await page.getByTitle("Permanently purge").click()
  await expect.poll(() => purgeCalls).toBe(1)
  await expect(page.getByText("archived memory")).toHaveCount(0)
})

test("clears and ignores a late recall response after switching projects", async ({ page }) => {
  let releaseRepoA: (() => void) | undefined
  let repoARequested = false
  const repoADelayed = new Promise<void>((resolve) => { releaseRepoA = resolve })
  await installApiMocks(page, {
    scopes: [projectA, projectB],
    onRequest: async (url, route) => {
      if (url.pathname !== "/recall" || route.request().method() !== "POST") return false
      const body = route.request().postDataJSON() as { repo_id?: string }
      if (body.repo_id === "repo-a") {
        repoARequested = true
        await repoADelayed
        return json(route, [memory("stale-recall", "stale repo A result", "episodic")])
      }
      return json(route, [memory("repo-b-recall", "repo B result", "episodic", "repo-b")])
    },
  })

  await page.goto("/dashboard/recall?repo_id=repo-a")
  await page.getByPlaceholder("What did I decide about authentication?").fill("stale")
  await page.getByRole("button", { name: "Search" }).click()
  await expect.poll(() => repoARequested).toBe(true)
  await page.getByRole("combobox", { name: "Project" }).selectOption("repo-b")
  await expect(page.getByText("Try searching for:")).toBeVisible()
  releaseRepoA?.()
  await page.waitForTimeout(100)
  await expect(page.getByText("stale repo A result")).toHaveCount(0)
})

test("refreshes project scopes after a mutation and falls back when the selected scope disappears", async ({ page }) => {
  let currentScopes: Scope[] = [projectA]
  let currentProjects: unknown[] = [{
    id: projectA.id,
    name: projectA.name,
    description: "Test project",
    tech_stack: [],
    status: "active",
    created_at: "2026-01-01T00:00:00Z",
  }]
  await installApiMocks(page, {
    scopes: currentScopes,
    projects: currentProjects,
    onRequest: async (url, route) => {
      if (url.pathname === "/repos/scopes") return json(route, currentScopes)
      if (url.pathname === "/repos" && route.request().method() === "GET") return json(route, currentProjects)
      if (url.pathname === "/repos" && route.request().method() === "POST") {
        currentScopes = [projectA, projectB]
        currentProjects = [...currentProjects, {
          id: projectB.id,
          name: projectB.name,
          description: "Test project",
          tech_stack: [],
          status: "active",
          created_at: "2026-01-01T00:00:00Z",
        }]
        return json(route, currentProjects[1])
      }
      if (url.pathname === "/repos/repo-a/archive") {
        currentScopes = [projectB]
        currentProjects = [currentProjects[1]]
        return json(route, { id: "repo-a", status: "archived" })
      }
      return false
    },
  })
  await page.goto("/dashboard/projects?repo_id=repo-a")
  await page.getByLabel("Name").fill("Project B")
  await page.getByLabel("ID").fill("repo-b")
  await page.getByRole("button", { name: "Create", exact: true }).click()
  await expect(page.getByRole("combobox", { name: "Project" }).locator("option", { hasText: "Project B" })).toBeAttached()

  await page.getByRole("row").filter({ hasText: "Project A" }).getByTitle("Archive").click()
  await expect(page.getByRole("combobox", { name: "Project" })).toHaveValue("repo-b")
})

test("discards a late graph response from the previous repository", async ({ page }) => {
  let releaseRepoA: (() => void) | undefined
  const repoADelayed = new Promise<void>((resolve) => { releaseRepoA = resolve })
  await installApiMocks(page, {
    scopes: [projectA, projectB],
    graph: {
      "repo-b": { nodes: [{ id: "b-1", label: "repo B node", layer: "episodic" }], links: [] },
    },
    onRequest: async (url, route) => {
      if (url.pathname !== "/graph") return false
      if (url.searchParams.get("repo_id") === "repo-a") {
        await repoADelayed
        return json(route, { nodes: [{ id: "a-1", label: "stale repo A node", layer: "episodic" }], links: [] })
      }
      return json(route, { nodes: [{ id: "b-1", label: "repo B node", layer: "episodic" }], links: [] })
    },
  })
  await page.goto("/dashboard/graph?repo_id=repo-a")
  await page.getByRole("combobox", { name: "Project" }).selectOption("repo-b")
  await expect(page.getByTestId("graph-node-labels")).toContainText("repo B node")
  releaseRepoA?.()
  await page.waitForTimeout(100)
  await expect(page.getByTestId("graph-node-labels")).not.toContainText("stale repo A node")
})

test("clears a loaded graph while the next repository response is delayed", async ({ page }) => {
  let releaseRepoB: (() => void) | undefined
  let repoBRequested = false
  const repoBDelayed = new Promise<void>((resolve) => { releaseRepoB = resolve })
  await installApiMocks(page, {
    scopes: [projectA, projectB],
    onRequest: async (url, route) => {
      if (url.pathname !== "/graph") return false
      if (url.searchParams.get("repo_id") === "repo-b") {
        repoBRequested = true
        await repoBDelayed
        return json(route, { nodes: [{ id: "b-1", label: "repo B node", layer: "episodic" }], links: [] })
      }
      return json(route, { nodes: [{ id: "a-1", label: "repo A node", layer: "episodic" }], links: [] })
    },
  })

  await page.goto("/dashboard/graph?repo_id=repo-a")
  await expect(page.getByTestId("graph-node-labels")).toContainText("repo A node")
  await page.getByRole("combobox", { name: "Project" }).selectOption("repo-b")
  await expect.poll(() => repoBRequested).toBe(true)
  await expect(page.getByTestId("graph-node-labels")).toHaveText("")
  await expect(page.getByTestId("graph-settlement")).toContainText("0 of 0 nodes visible")

  releaseRepoB?.()
  await expect(page.getByTestId("graph-node-labels")).toContainText("repo B node")
  await expect(page.getByTestId("graph-node-labels")).not.toContainText("repo A node")
})

test("draws relationship particles when a linked node is selected", async ({ page }) => {
  await installApiMocks(page, {
    graph: {
      "repo-a": {
        nodes: [
          { id: "one", label: "one", layer: "episodic" },
          { id: "two", label: "two", layer: "semantic" },
        ],
        links: [{ source: "one", target: "two", value: 0.8, label: "supports" }],
      },
    },
  })
  await page.goto("/dashboard/graph?repo_id=repo-a")
  await expect(page.getByTestId("graph-link-count")).toHaveText("1 graph link")
  await expect(page.getByTestId("graph-settlement")).toContainText("2 of 2 nodes visible")
  await page.evaluate(() => {
    const prototype = CanvasRenderingContext2D.prototype as CanvasRenderingContext2D & {
      __phase3ArcCalls?: number
      __phase3OriginalArc?: CanvasRenderingContext2D["arc"]
    }
    if (!prototype.__phase3OriginalArc) {
      prototype.__phase3OriginalArc = prototype.arc
      prototype.arc = function (...args) {
        prototype.__phase3ArcCalls = (prototype.__phase3ArcCalls || 0) + 1
        return prototype.__phase3OriginalArc?.apply(this, args) as void
      }
    }
    prototype.__phase3ArcCalls = 0
  })
  await page.getByTestId("graph-node-control-one").evaluate((element) => (element as HTMLButtonElement).click())
  await expect(page.getByText("1 connections")).toBeVisible()
  await expect(page.getByText("supports")).toBeVisible()
  await expect.poll(() => page.evaluate(() => {
    const prototype = CanvasRenderingContext2D.prototype as CanvasRenderingContext2D & { __phase3ArcCalls?: number }
    return prototype.__phase3ArcCalls || 0
  })).toBeGreaterThan(6)
})

test("force layout settles and restarts after an intentional graph filter change", async ({ page }) => {
  await installApiMocks(page, {
    graph: {
      "repo-a": {
        nodes: [
          { id: "one", label: "one", layer: "episodic" },
          { id: "two", label: "two", layer: "semantic" },
        ],
        links: [],
      },
    },
  })
  await page.goto("/dashboard/graph?repo_id=repo-a")
  await expect(page.getByTestId("graph-settlement")).toContainText("Layout settled", { timeout: 10000 })
  await page.getByRole("button", { name: "Show graph filters" }).click()
  await page.getByTestId("graph-filter-semantic").click()
  await expect(page.getByTestId("graph-settlement")).toContainText("1 of 2 nodes visible")
  await expect(page.getByTestId("graph-settlement")).toContainText("Layout settled", { timeout: 10000 })
})
