---
name: visp-memory-capture
description: Capture a completed Codex session into Visp Memory using local direct mode by default, including changed files, decisions, tests, failures, and follow-up intents.
---

# Visp Memory Capture

Use this skill after meaningful work is completed.

## Workflow

1. Summarize what changed, why it changed, and which files were touched.
2. Record durable decisions with `visp-memory decision`.
3. Record implementation notes with `visp-memory record`.
4. Record follow-up work with `visp-memory goal` or `visp-memory working` when appropriate.
5. Record the outcome against the current intent when the work is finished:
   - `visp-memory done` records a completion outcome for the live task; `visp-memory intent complete <id>` or `visp-memory intent close <id>` does the same for a named intent.
   - Prefer MCP tools `memory_update_intent`, `memory_close_intent`, or `memory_done` when available.
   - Use API endpoints only when server mode is already configured.
   - This records completion **in memory**. It does not make the task done: the project's own checks and review decide that.
6. Include tests run and failures fixed.
7. Prefer `--repo <repo-id>` when the project scope is known.

Do not capture secrets, API keys, tokens, or raw credential errors.
