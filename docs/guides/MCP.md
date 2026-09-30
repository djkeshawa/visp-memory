# Connect an assistant

For multi-project local clients using one server, see the [shared server guide](SHARED_SERVER.md).

MCP lets an assistant call Visp Memory tools. Hooks provide additional context at
supported session/file events. Initializing a store does not install either integration.

## Install and connect

```bash
pip install "visp-memory[mcp,capture]"
visp-memory init
```

Run commands in the project whose memory you want to use. To connect it to a
running shared server and install Codex integration:

```bash
visp-memory connect --agent-config codex
```

The installer writes project-level `AGENTS.md` guidance and a global managed MCP
entry in `~/.codex/config.toml` that contains only `command = "visp-memory-mcp"`,
`args = []` and `env = { VISP_MEMORY_AGENT = "codex" }`. It does not pin a working
directory, repository, storage mode, or server URL. Codex is expected to launch
the MCP server in the active session directory, where it discovers that
project's `visp-memory.yaml`; if your Codex version does not, project discovery
will not work, so set `cwd` in the block or use per-project Codex config.
Repeating the command from another project leaves the global block unchanged.

Re-running this command, or `visp-memory hooks install codex`, over an older
block that pinned `cwd`, `VISP_MEMORY_REPO_ID`, or the storage mode or URL
rewrites it after saving a backup, and this changes behavior for every project
that uses Codex. The command prints a migration note. An existing
`VISP_MEMORY_EMBEDDING_PROVIDER = "noop"` entry is preserved.

Restart the assistant after changing MCP configuration. Configure server
credentials and project access as described in [authentication](AUTHENTICATION.md).
In client mode, storage and embedding generation happen on the server.

### Other MCP clients

Use `visp-memory-mcp` as the stdio command. A client that accepts an `mcpServers`
configuration can use the following shape; working-directory support varies by client:

```json
{
  "mcpServers": {
    "visp-memory": {
      "command": "visp-memory-mcp",
      "env": {
        "VISP_MEMORY_AGENT": "your-client-name",
        "VISP_MEMORY_MCP_PROFILE": "core"
      }
    }
  }
}
```

The command must be on the client's PATH and the client must launch it with the
project as its working directory so normal `visp-memory.yaml` discovery works.
Check the client's own configuration format when adapting this example.

## The everyday workflow

| Moment | Tools |
|---|---|
| Start a task | `memory_prepare_task`, `memory_session_start` |
| Search or inspect a file | `memory_recall`, `memory_file_context`, `memory_before_change` |
| Record a useful result | `memory_record`, `memory_decision`, `memory_warn`, `memory_after_work` |
| Keep direction | `memory_goal`, `memory_working_on` |
| Report an externally completed task | `memory_update_intent` with `workflow_report` (full profile) |

For example, call `memory_prepare_task` with:

```json
{
  "task": "Fix session expiration in the dashboard",
  "files": ["src/auth.py"],
  "token_budget": 1200,
  "format": "text"
}
```

It returns cited context, relevant intent, constraints, contradictions, and
unknowns. Reuse its fingerprint to avoid repeating an unchanged brief. The
[architecture guide](../reference/ARCHITECTURE.md#search-and-task-context) explains selection.

For CLI direction, use `visp-memory goal`, `visp-memory focus`, and
`visp-memory working`. These intents are direction, not permission, and
`visp-memory done` or a generic `complete` records an advisory outcome; see [workflow reports](WORKFLOW_REPORTS.md) for how an
actual status change is recorded.

## Hooks and project guidance

```bash
visp-memory hooks install claude-code
```

Claude Code integration installs `SessionStart` context and `PreToolUse` context
for Read/Edit/Write events in the project's `.claude/settings.json`. File context
is deduplicated within the session. Hook failures do not block the assistant;
the returned memory is context, never a permission decision.

Add `--mcp` to merge a `visp-memory` server into the project's `.mcp.json`
without replacing other servers. Use one of these two alternatives, not both:

```bash
# Local mode
visp-memory hooks install claude-code --mcp

# Shared server (also installs the hooks and .mcp.json entry)
visp-memory connect --agent-config claude-code
```

Without `--mcp`, the existing Claude Code hook installation behavior is
unchanged.

Codex, Cursor, Aider, and generic integration support varies; see
[feature status](../reference/FEATURE_STATUS.md). Codex users can also install the
bundled [Codex plugin](../../plugins/visp-memory/README.md). Review generated project instructions.
Use the installed command's help for adapter-specific options:

```bash
visp-memory hooks --help
visp-memory hooks update codex --task "fix session expiry" --file src/auth.py
visp-memory hooks uninstall codex
```

Use `hooks install <tool> --dry-run` or `hooks uninstall <tool> --dry-run` to
preview any adapter without changing instructions, settings, backups, or memory
storage. Setup returns a nonzero exit code when a required component cannot be
installed; review the reported component before retrying.

To preview importing existing instruction files, run
`visp-memory ingest-instructions --dry-run`, then omit `--dry-run` to import.
These records have external provenance and remain quarantined from automatic
prompt injection until reviewed; they are not trusted instructions merely because
an instruction file supplied them.
Generated Claude Code, Codex, and Cursor context blocks are excluded from this
import so previously retrieved memory does not become a new source.

For CLI inspection, use `visp-memory remember --repo my-project` or
`visp-memory recall "session expiry" --repo my-project`.

## Ranking, selection, and time

`memory_recall`, `memory_prepare_task`, and query-based `memory_context` accept
`ranking_strategy` (`default`, `hybrid`, or `hybrid_union`). The two context tools
also accept `context_selection` (`default` or `coverage`) and an ISO 8601 `as_of`
timestamp. The CLI equivalents are `--ranking-strategy`, `--context-selection`, and
`--as-of`:

```bash
visp-memory brief "session expiry" --repo my-project --ranking-strategy hybrid
```

`memory_context` requires a query for a hybrid strategy or an `as_of` time, rather
than silently applying them to its legacy project summary. Omitted options keep the
defaults. The [architecture guide](../reference/ARCHITECTURE.md#ranking-strategies)
describes each option; hybrid modes examine more candidates and can add latency.

For context supplied automatically to an assistant, prefer `memory_prepare_task`;
query-based `memory_context` is an
[inspection surface](../reference/TRUST.md#provenance-and-quarantine) that can
return quarantined records.

## Verify the connection

1. Run `visp-memory doctor` in the project.
2. Record a harmless, recognizable decision with `visp-memory decision`.
3. In a new assistant session, ask it to recall that decision through MCP.
4. Confirm the result belongs to the expected project and cites the stored source.

If no tools appear, check the MCP extra, executable path, client configuration,
and whether the assistant was restarted. If the wrong memories appear, inspect
the repository ID and data path. If semantic search is unavailable, check both
[the provider and vector index](STORAGE.md#how-embeddings-work).

## Tool profiles

| Profile | Advertised tools | Purpose |
|---|---|---|
| `readonly` | 6 | Retrieval and context |
| `core` (default) | 17 | Everyday recall and capture |
| `full` | 36 | Advanced and maintenance tools, including workflow reports |

Set `VISP_MEMORY_MCP_PROFILE` to choose a profile. Profiles affect advertisement,
**not authorization**: a hidden tool can still be called by name. Enforce access
through server credentials and scopes. See [server authentication](AUTHENTICATION.md).

The [footprint test](../../tests/interfaces/test_mcp_profile_footprint.py) measures
serialized schemas and checks a 35–45% reduction for core versus full. This is a
schema-size measurement, not a token-billing or coding-quality result.

## HTTP transport

`visp-memory-mcp-http` runs the stateless HTTP transport. Configure
`VISP_MEMORY_MCP_HTTP_HOST` (default `127.0.0.1`), `VISP_MEMORY_MCP_HTTP_PORT`
(default `8848`), and `VISP_MEMORY_MCP_HTTP_TOKEN`. A bearer token is required
before binding beyond loopback.

Clients should send project scope explicitly. Requests to `/mcp` do not depend
on server-side session IDs. Model tasks use the configured server provider;
client sampling is unavailable on this stateless transport. The retrieval policy
itself does not call a model.
