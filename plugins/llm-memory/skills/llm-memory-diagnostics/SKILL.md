---
name: llm-memory-diagnostics
description: Diagnose LLM Memory local/server/provider health for Codex workflows without leaking secrets. Use when memory recall, capture, embeddings, OpenRouter, OpenAI, Ollama, storage, or dashboard integration fails.
---

# LLM Memory Diagnostics

Use this skill when LLM Memory is not working or provider state is unclear.

## Workflow

1. Run `llm-memory doctor`.
2. Run `llm-memory providers`.
3. If a specific provider is configured, run `llm-memory providers test <provider>`.
4. Treat configured credentials as separate from connected credentials.
5. Explain 401/403 as credential/access failures without printing tokens.
6. If cloud providers fail, offer local/noop/Ollama mode for basic Codex memory workflows.

Never print API keys, JWTs, or provider tokens.
