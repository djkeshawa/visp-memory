# LLM Memory Codex Plugin

This plugin gives Codex local-first workflows for LLM Memory.

Default mode is direct local mode:

- Use the installed `llm-memory` CLI or Python package.
- Use the bundled `.mcp.json` to expose LLM Memory tools directly in Codex.
- Recall the latest memory with `llm-memory remember` or the MCP `memory_remember` tool.
- Read project memory for the current repository.
- Capture decisions, changed files, tests, failures, and follow-up intents.
- Preview memory strength/decay risk before applying decay.
- Update, complete, or close stale intents from MCP or the dashboard.
- Run diagnostics with `llm-memory doctor`.
- Avoid requiring OpenRouter, OpenAI, or a running API server for basic usage.

Server mode remains available when a team deployment or dashboard is configured.
