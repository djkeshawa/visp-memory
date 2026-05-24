---
name: llm-memory-capture
description: Capture a completed Codex session into LLM Memory using local direct mode by default, including changed files, decisions, tests, failures, and follow-up intents.
---

# LLM Memory Capture

Use this skill after meaningful work is completed.

## Workflow

1. Summarize what changed, why it changed, and which files were touched.
2. Record durable decisions with `llm-memory decision`.
3. Record implementation notes with `llm-memory record`.
4. Record follow-up work with `llm-memory goal` or `llm-memory working` when appropriate.
5. Close or complete the current intent when the work is finished:
   - Prefer MCP tools `memory_update_intent`, `memory_close_intent`, or `memory_done` when available.
   - Use API endpoints only when server mode is already configured.
6. Include tests run and failures fixed.
7. Prefer `--repo <repo-id>` when the project scope is known.

Do not capture secrets, API keys, tokens, or raw credential errors.
