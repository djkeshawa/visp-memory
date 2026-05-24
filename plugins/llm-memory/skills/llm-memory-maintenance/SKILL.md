---
name: llm-memory-maintenance
description: Maintain LLM Memory data quality and indexes from Codex, including memory strength/decay preview, intent lifecycle review, embedding reindex dry-runs, duplicate review, archived memories, audit logs, and exports.
---

# LLM Memory Maintenance

Use this skill when Codex should help maintain memory quality.

## Workflow

1. Run provider diagnostics before reindexing.
2. Use a dry-run before rebuilding embeddings.
3. Preview memory decay before applying it:
   - In MCP mode, use `memory_decay_preview` before `memory_decay`.
   - In server mode, call `GET /quality/decay-preview`.
   - Treat `likely_to_decay` memories as review candidates, not automatic deletion candidates.
4. Review active intents and close stale ones:
   - In MCP mode, use `memory_list_intents`, `memory_update_intent`, and `memory_close_intent`.
   - In server mode, use `GET /intents?status=all`, `PATCH /intents/{id}`, and `POST /intents/{id}/close`.
5. Review duplicate candidates before merging or deleting anything.
6. Prefer archiving over hard deletion unless the user explicitly asks to delete.
7. Export a backup before bulk maintenance.
8. Check audit logs after state-changing maintenance.

Avoid destructive actions without explicit user approval.
