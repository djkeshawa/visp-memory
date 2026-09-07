# Feature status

The supported path is **one developer, one local repository, SQLite storage**.
Stable means supported for normal use; beta interfaces may change; experimental
features need more field use. Frozen features are retained but not actively extended.

| Area | Status | Notes |
|------|--------|-------|
| CLI (`record`, `recall`, `decision`, `warn`, `goal`, `context`, `brief`) | Stable | The primary interface. |
| SQLite storage | Stable | Default. No external service. |
| MCP server (`core` profile) | Stable | Default surface, 17 tools. `readonly` exposes 6; `full` exposes 36. |
| Keyword recall (no embeddings) | Stable | Default when no provider is configured. |
| Semantic recall (OpenAI / OpenRouter / Ollama / sentence-transformers) | Stable | Requires a working provider and vector index; otherwise keyword fallback. |
| Git capture (`sync_history`, commit/merge hooks) | Stable | Foundation of first-run bootstrap. |
| Claude Code auto-injection hooks | Beta | Injection policy is new; see below. |
| Precision-gated injection (budget, floor, complexity gate) | Beta | Newly introduced; benchmarked, not yet field-proven. |
| Trust decay and provenance quarantine | Beta | Defends against poisoned/stale memory. |
| `visp-memory audit` | Beta | Inspect provenance, supersession, and quarantine. |
| Structural code anchoring | Experimental | Anchors memories to files/symbols; staleness detection. |
| Cursor / Aider / Codex / generic hooks | Experimental | Less exercised than the Claude Code path. |
| REST API + dashboard | Experimental | Works; secondary to the CLI/MCP path. |
| [Dreaming cycles](development/DREAMING.md) | Beta | Scheduled SQLite/Neo4j cleanup, recoverable exact merges, and review suggestions. No model or vector index required. |
| [External workflow reports](development/WORKFLOW_REPORTS.md) | Beta | Explicit assistant/workflow status, evidence, and ordered history; SQLite and Neo4j. |
| ArcadeDB backend | Frozen | Embedded graph storage. Use SQLite unless you need it. |
| Neo4j backend | Beta | Optional Evidence graph, workflow reports, dreaming, and graph backup. Requires an external service; SQLite remains default. |
| Teams, users, JWT auth | Frozen | Multi-user server features. |
| Cross-repo aggregation | Frozen | Depends on the team server. |

Dreaming schedules start paused on new installations. Completion reports need
an explicit integration from the owning assistant or workflow. Neither feature
runs a model or independently certifies that work is complete.

These statuses describe support, not measured coding benefit. See
[benchmarks](BENCHMARK.md) for the evidence and [installation](deployment/PACKAGING.md)
for deployment choices. Report problems through [GitHub Issues](https://github.com/djkeshawa/visp-memory/issues).
