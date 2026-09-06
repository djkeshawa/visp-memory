# Feature Status

What is supported, what is early, and what is frozen.

- **Stable** — works for normal use, covered by tests, safe to depend on.
- **Beta** — works and is tested, but the interface may change.
- **Experimental** — usable, but under-exercised in the real world. Expect rough edges.
- **Frozen** — implemented and tested, but not actively developed. Kept working, not
  extended, until the supported path earns its keep. Report bugs; do not expect
  features.

## The supported path

The project optimises for one setup: **a single developer, on their own machine, using
a coding assistant against one repository.** Everything below that is Stable belongs to
that path. Anything outside it is honestly labelled rather than quietly implied.

| Area | Status | Notes |
|------|--------|-------|
| CLI (`record`, `recall`, `decision`, `warn`, `goal`, `context`, `brief`) | Stable | The primary interface. |
| SQLite storage | Stable | Default. No external service. |
| MCP server (`core` profile) | Stable | Default surface, 17 tools. `readonly` exposes 6; `full` exposes 36. |
| Keyword recall (no embeddings) | Stable | Default when no provider is configured. |
| Semantic recall (OpenAI / OpenRouter / Ollama / sentence-transformers) | Stable | Auto-selected when available. |
| Git capture (`sync_history`, commit/merge hooks) | Stable | Foundation of first-run bootstrap. |
| Claude Code auto-injection hooks | Beta | Injection policy is new; see below. |
| Precision-gated injection (budget, floor, complexity gate) | Beta | Newly introduced; benchmarked, not yet field-proven. |
| Trust decay and provenance quarantine | Beta | Defends against poisoned/stale memory. |
| `visp-memory audit` | Beta | Inspect provenance, supersession, and quarantine. |
| Structural code anchoring | Experimental | Anchors memories to files/symbols; staleness detection. |
| Cursor / Aider / Codex / generic hooks | Experimental | Less exercised than the Claude Code path. |
| REST API + dashboard | Experimental | Works; secondary to the CLI/MCP path. |
| [Dreaming cycles](development/DREAMING.md) | Beta | Scheduled SQLite cleanup, recoverable exact merges, and review suggestions. No model or vector index required. |
| ArcadeDB backend | Frozen | Embedded graph storage. Use SQLite unless you need it. |
| Neo4j backend | Frozen | For shared deployments; requires an external service. |
| Teams, users, JWT auth | Frozen | Multi-user server features. |
| Cross-repo aggregation | Frozen | Depends on the team server. |

## Why things are frozen

The frozen features work — they have tests and they pass. They are frozen because a
project maintained by one person cannot credibly support twelve surfaces at once, and
because none of them helps the first user in their first minute.

They are the long-term differentiator: shared team memory and cross-repository context
are exactly what a per-machine, per-tool memory cannot provide. They come back when the
single-developer path is good enough that people actually keep it installed.

If you depend on a frozen feature, say so in an issue. That is the signal that unfreezes
it.

## Deliberate non-goals

- **A general-purpose chat memory.** This project stores what a codebase learned, not
  what a user prefers. For assistant-personalisation memory, other tools fit better.
- **Replacing your assistant's built-in memory.** Claude Code's auto memory and
  `CLAUDE.md` are good at what they do. This complements them: it is portable across
  machines and tools, and it curates rather than accumulates.
- **Maximum recall.** Injecting more memory is easy and usually harmful. See
  [docs/development/INJECTION_POLICY.md](development/INJECTION_POLICY.md).
