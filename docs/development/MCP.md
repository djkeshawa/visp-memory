# Connect an assistant

MCP lets an assistant call Visp Memory tools. Hooks provide additional context at
supported session/file events. Initializing a store does not install either integration.

## Install and connect

```bash
pip install "visp-memory[mcp,capture]"
visp-memory init
```

Run commands in the project whose memory you want to use. For Codex connected to
a running Visp Memory server:

```bash
visp-memory hooks install codex \
  --server-url http://127.0.0.1:8000 \
  --repo-id my-project
```

The installer writes managed MCP configuration and project-level `AGENTS.md`
guidance. Restart the assistant to load the new configuration. Configure server
credentials and project access as described in [authentication](../deployment/AUTH.md).
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
        "VISP_MEMORY_REPO_ID": "my-project",
        "VISP_MEMORY_STORAGE_DATA_DIR": "/absolute/path/to/project/.visp-memory/data",
        "VISP_MEMORY_EMBEDDING_PROVIDER": "noop",
        "VISP_MEMORY_MCP_PROFILE": "core"
      }
    }
  }
}
```

Use absolute paths and the same project ID as your store. The command must be on
the client's PATH. Check the client's own configuration format when adapting this example.

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
[architecture guide](ARCHITECTURE.md#search-and-task-context) explains selection.

For CLI direction, use `visp-memory goal`, `visp-memory focus`, and
`visp-memory working`. `visp-memory done` records an advisory outcome. These
intents are direction, not permission. Generic `complete` operations are also advisory. To mirror an
actual task status change, send an explicit [workflow report](WORKFLOW_REPORTS.md).
Installing the integration does not make every assistant send these reports automatically.

## Hooks and project guidance

```bash
visp-memory hooks install claude-code
```

Claude Code integration installs `SessionStart` context and `PreToolUse` context
for Read/Edit/Write events in the project's `.claude/settings.json`. File context
is deduplicated within the session. Hook failures do not block the assistant;
the returned memory is context, never a permission decision.

Codex, Cursor, Aider, and generic integration support varies; see
[feature status](../FEATURE_STATUS.md). Review generated project instructions.
Use the installed command's help for adapter-specific options:

```bash
visp-memory hooks --help
visp-memory hooks update codex --task "fix session expiry" --file src/auth.py
visp-memory hooks uninstall codex
```

To preview importing existing instruction files, run
`visp-memory ingest-instructions --dry-run`, then omit `--dry-run` to import.
These records have external provenance and remain quarantined from automatic
prompt injection until reviewed; they are not trusted instructions merely because
an instruction file supplied them.

For CLI inspection, use `visp-memory remember --repo my-project` or
`visp-memory recall "session expiry" --repo my-project`.

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
through server credentials and scopes. See [server authentication](../deployment/AUTH.md).

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
