# Assistant Guidance

This project can generate project-scoped assistant instructions for tools such
as Codex. The generated block is intentionally small so it can be reviewed in
the repository alongside other project instructions.

## Codex

Install or refresh Codex guidance with:

```bash
visp-memory hooks install codex --repo-id <repo-id>
visp-memory hooks update codex --task "<task>" --file <path>
```

The managed `AGENTS.md` section tells assistants to:

- recall the latest memory with `visp-memory remember --repo <repo-id>` or MCP
  `memory_remember`;
- search project memory with `visp-memory recall "<task>" --repo <repo-id>` or
  MCP `memory_recall`;
- inspect file-specific context with `visp-memory inject --file <path>
  --task "<task>"` or MCP `memory_file_context`;
- record meaningful outcomes with `visp-memory record`, `visp-memory decision`,
  `visp-memory learn`, or MCP `memory_after_work`;
- keep memory scoped to the configured repository unless cross-project memory is
  intentional;
- avoid printing secrets, prompts, responses, API keys, or unrelated team memory.

Generated guidance should stay project-scoped, reviewable, and limited to
current command names. Remove it with:

```bash
visp-memory hooks uninstall codex
```
