# Visp Memory Codex Plugin

This plugin gives Codex local-first workflows for Visp Memory.

Default mode is direct local mode. The bundled MCP server reads the project's
`visp-memory.yaml` (created by `visp-memory init`) from the working directory, so it
uses that project's ID and local store:

- Use the installed `visp-memory` CLI or Python package.
- Use the bundled `.mcp.json` to expose Visp Memory tools directly in Codex.
- Recall the latest memory with `visp-memory remember`; prepare cited task context with the default-profile MCP `memory_prepare_task` tool.
- Read project memory for the current repository.
- Capture decisions, changed files, tests, failures, and follow-up intents.
- Preview memory strength/decay risk before applying decay.
- Update, complete, or close stale intents from the dashboard, or from MCP with the
  full tool profile.
- Run diagnostics with `visp-memory doctor`.
- Avoid requiring OpenRouter, OpenAI, or a running API server for basic usage.

Server mode remains available when a team deployment or dashboard is configured.
