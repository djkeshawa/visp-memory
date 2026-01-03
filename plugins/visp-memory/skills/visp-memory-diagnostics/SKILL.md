---
name: visp-memory-diagnostics
description: Diagnose Visp Memory local/server/provider health for Codex workflows without leaking secrets. Use when memory recall, capture, embeddings, OpenRouter, OpenAI, Ollama, storage, or dashboard integration fails.
---

# Visp Memory Diagnostics

Use this skill when Visp Memory is not working or provider state is unclear.

## Workflow

1. Run `visp-memory doctor`.
2. Run `visp-memory providers`.
3. If a specific provider is configured, run `visp-memory providers test <provider>`.
4. Treat configured credentials as separate from connected credentials.
5. Explain 401/403 as credential/access failures without printing tokens.
6. If cloud providers fail, offer local/noop/Ollama mode for basic Codex memory workflows.

Never print API keys, JWTs, or provider tokens.
