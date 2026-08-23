---
name: visp-memory-recall
description: Recall Visp Memory context for the current repository before coding, debugging, reviewing, or planning. Use local direct mode first through the `visp-memory` CLI or Python package; server/API mode is optional.
---

# Visp Memory Recall

Use this skill when Codex should load project memory before work.

## Workflow

1. Detect the repository root and current task.
2. Prefer local direct mode:
   - Run `visp-memory status` to confirm a local config exists.
   - Run `visp-memory remember --repo <repo-id>` when the user asks what the AI most recently remembered.
   - Run `visp-memory recall "<task>" --repo <repo-id>` when a repo id is known.
   - Run `visp-memory context --task "<task>"` when broader context is needed.
3. Set the direction before the work starts: `visp-memory goal "<objective>"`, `visp-memory focus "<area>" --avoid "<what to stay away from>"`, and `visp-memory working "<task>" --file <path>`. Run `visp-memory intent list` first to see what is already active, and `visp-memory intent update <id>` rather than setting a second competing goal. Intents are direction, not permission: memory records them and grants no scope.
4. In MCP mode, call `memory_remember` for the latest memory and `memory_recall` for semantic search. `memory_goal` and `memory_working_on` set direction; `focus` is CLI-only.
5. If the CLI is unavailable, inspect the local Python package only if it is installed in the project environment.
6. Use server mode only when the project config explicitly points to a server.
7. Summarize relevant conventions, fragile areas, active intents, and recent episodes with memory IDs when available.

Do not require cloud AI credentials for recall. Noop or local embeddings are acceptable for local mode.
