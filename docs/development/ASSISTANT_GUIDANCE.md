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
- set and keep the current direction with `visp-memory goal`,
  `visp-memory focus`, `visp-memory working` and `visp-memory done`, or MCP
  `memory_goal`, `memory_working_on` and `memory_done` — `focus` is CLI-only;
- record meaningful outcomes with `visp-memory record`, `visp-memory decision`,
  `visp-memory bug`, `visp-memory learn`, `visp-memory warn`, or MCP
  `memory_after_work`;
- keep memory scoped to the configured repository unless cross-project memory is
  intentional;
- avoid printing secrets, prompts, responses, API keys, or unrelated team memory.

## Intent is direction, not permission

The intent verbs record what the work is aimed at, what to avoid, and which task
is live. That is the context an assistant otherwise re-derives every session, and
the injected context block now carries one line of it.

Memory is non-authoritative. Setting a goal grants no scope, certifies no
evidence and declares nothing ready; `visp-memory done` records a completion
outcome *in memory* and does not make a task done — the project's own checks and
review decide that. Generated guidance must never say otherwise.

The verb names above are rendered from one catalogue,
`src/visp_memory/core/verbs.py`. Every adapter template imports it, so a verb is
added or renamed in exactly one place. They were hand-written per template
before, and the intent verbs were missing from all of them.

## Making the gap visible

`visp-memory doctor` reports intent adoption: a store holding memories with zero
intents is called out as a finding, with the verbs to run. It is a description of
the store, never a verdict on the work.

Generated guidance should stay project-scoped, reviewable, and limited to
current command names. Remove it with:

```bash
visp-memory hooks uninstall codex
```
