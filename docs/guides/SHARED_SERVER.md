# Shared local server

Use a shared server when several assistant processes need the same local storage
while keeping each project's records scoped to its own repository. It is a
single-user, same-machine setup; team accounts and cross-project aggregation are
not provided.

## Start the server

```bash
visp-memory serve --shared
```

The default store is under `~/.visp-memory`. The server creates and pins a
project-neutral `config.yaml` there, and removes any inherited repository ID.
Each request must supply its project scope; there is no server-wide `repo_id`.
Keep the shared config free of project settings.

## Connect projects

Run in each project:

```bash
visp-memory connect
visp-memory connect --agent-config claude-code
visp-memory connect --agent-config codex
```

`connect` selects the project repository ID and writes client storage settings
to `visp-memory.yaml`. Use `--migrate-local` to copy existing local records to
the server before switching that project. YAML comments are not preserved when
connect rewrites the file. The agent config option installs the matching
project integration; review its generated files.

Claude Code can also be installed with `visp-memory hooks install claude-code --mcp`,
which adds a server to the project's `.mcp.json`. Codex uses one global block in
`~/.codex/config.toml` that names no project: Codex starts the MCP server in the
session's working directory, which picks up that project's `visp-memory.yaml`.
See [MCP and hooks](MCP.md) for host setup details.

## Writers and attribution

A running server owns its store. While it runs, a local-mode process (CLI, stdio
MCP, hooks), offline maintenance, or the separate HTTP MCP program cannot open
the same data directory, and a server will not start while local-mode writers
hold it. Local-mode processes may still share a directory with each other when
no server owns it. Connect each project in client mode instead. The guard uses
OS file locks; NFS is unsupported.

The process that serves holds the lock, so `visp-memory serve --reload` works: the
reloader only imports the app, and the child it spawns takes the lock. One server
process owns a store. `uvicorn --workers N` with N above 1 is not supported: the
first worker serves and the others are refused with a message naming the sibling
worker, and uvicorn keeps restarting them. Run a single worker.

Requests can identify an agent and session. The server records these labels in
`written_by` on memories, evidence, and intents. This is informational
attribution only: it is supplied by the client, can be forged, and grants no
authority. It does not change provenance or trust.

## Maintenance and boundaries

Local maintenance uses a random owner token stored with owner-only file
permissions under `~/.visp-memory/run`. This proves access by the same OS user
through that file, not a separate application identity. The dashboard cannot
read the token; dashboard maintenance requires an account.

Projects remain isolated: recall, context, and list operations use the
project's configured scope. Unscoped requests are refused. Stdio MCP also
refuses a `repo_id` override unless `VISP_MEMORY_MCP_ALLOW_REPO_OVERRIDE` is
enabled. Shared-server setup and authentication details are in
[authentication](AUTHENTICATION.md); storage behavior is in [storage](STORAGE.md).

## Known limitation: trust tier of client writes

A client-mode write reaches the server over REST, so it gets the HTTP channel's
`external` tier, not the `assisted` tier the same stdio MCP write gets in local
mode. External records are never auto-injected ([trust](../reference/TRUST.md)).
The server cannot accept a client's claim of a stronger channel, because a
payload never grants itself a trusted tier.

