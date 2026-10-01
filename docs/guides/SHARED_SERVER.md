# Shared local server

Use a shared server when several assistant processes need the same local storage.
It is a single-user, same-machine setup: one person, one computer, one server.
Team accounts and cross-project aggregation are not provided.

Each client is configured with its own repository scope, and requests that name
no repository are refused. **This is scoping, not access control.** In open
local-owner mode (see [authentication](AUTHENTICATION.md#open-local-owner-mode))
every caller that reaches the loopback port is the local owner and passes every
repository check without a token, so any local process that can connect can read
or write any project by naming its `repo_id`. The owner token gates only the
[maintenance routes](AUTHENTICATION.md#owner-maintenance-on-a-local-server). If
projects must be kept apart from each other or from other local users, start the
server with accounts or tokens instead of open mode.

## Start the server

```bash
visp-memory serve --shared
```

`--shared` serves one project-neutral store:

- The default store is `~/.visp-memory/data`. `--data-dir <path>` (valid only
  with `--shared`) selects another directory.
- `~/.visp-memory/config.yaml` is created only if it is missing, with the content
  `storage: {data_dir: data}`. The server pins it through `VISP_MEMORY_CONFIG`,
  so a project's `visp-memory.yaml` in the working directory cannot leak its
  repository, provider or storage settings into the shared server. An existing
  file is used as it is.
- `VISP_MEMORY_REPO_ID` is removed from the server's environment. If the shared
  config sets `repo_id`, `serve --shared` refuses to start: remove `repo_id` and
  any other project settings from that file. Each request must name its project.
- `~/.visp-memory` is created private (mode `0700`).
- In open local-owner mode the server rejects requests whose `Host` header is not
  a loopback name (a DNS-rebinding guard). Cross-origin unsafe requests are also
  rejected.

## Connect projects

Start the server first; `connect` does not launch it. Run in each project:

```bash
visp-memory connect
visp-memory connect --agent-config claude-code
visp-memory connect --agent-config codex
```

`connect` selects the project repository ID and writes client storage settings
to the active project config. See [storage](STORAGE.md#connect-a-project-to-a-shared-server)
for how the ID, project root and server are resolved. Use `--migrate-local` to
copy existing local records to the server before switching that project. The
command prints the local source and original trust-tier counts first. All migrated
memories and Evidence receive the import channel's `external` provenance on the
client before sending; memory sources become `external` and prior approvals are
cleared. No `--yes` is required. Valid signed authority attestations remain intact
because their signed content, scope and Evidence hashes do not change. To re-approve
memories after owner review, use `PATCH /memories/{id}` to replace `provenance:*` tags
with `provenance:authored` (keep other tags) and set `source` to `authored`; only an
administrator or the owner-token holder may change a tier.
`visp-memory review accept` accepts pending proposals; it does not promote trust.
Other import callers keep their existing behavior. Simple YAML keeps comments;
complex YAML is backed up before rewriting, with a warning.
`--agent-config` installs the matching project integration; review its generated
files.

For Claude Code, choose one path. In local mode, run
`visp-memory hooks install claude-code --mcp`, which adds a server to the
project's `.mcp.json`. For the shared server, run
`visp-memory connect --agent-config claude-code`, which also installs the
hooks and `.mcp.json` entry. See [MCP and hooks](MCP.md) for host setup details.

### Codex

Codex uses one **global** block in `~/.codex/config.toml` that names no project:

```toml
# BEGIN LLM-MEMORY CODEX MCP
[mcp_servers.visp-memory]
command = "visp-memory-mcp"
args = []
env = { VISP_MEMORY_AGENT = "codex" }
# END LLM-MEMORY CODEX MCP
```

Codex is expected to launch the MCP server in the active session directory, where
it finds that project's `visp-memory.yaml`. If your Codex version does not,
project discovery will not work: set `cwd` in the block or use per-project
Codex config. `connect --agent-config codex` and `hooks install codex` both write
this global file, whichever project you run them in (`hooks install` accepts
`--config-path` to write elsewhere).

**Migration.** Older versions pinned `cwd`, `VISP_MEMORY_REPO_ID` and the storage
mode or URL in the block. Re-running `hooks install codex` or
`connect --agent-config codex` over such a block rewrites it after saving a
`config.toml.backup`, and the change applies to every project that uses Codex.
The command prints a migration note when it replaces a pinned block. An existing
`VISP_MEMORY_EMBEDDING_PROVIDER = "noop"` entry is preserved. `hooks install
codex --server-url` no longer has any effect; the URL comes from each project's
`visp-memory.yaml`.

## Writers and attribution

A running server owns its store. While it runs, a local-mode process (CLI, stdio
MCP, hooks), offline maintenance, or the separate HTTP MCP program cannot open
the same data directory, and a server will not start while local-mode writers
hold it. Local-mode processes may still share a directory with each other when
no server owns it. Connect each project in client mode instead. The guard covers
SQLite and ArcadeDB, plus the local `auth.db` and `lifecycle.db` files used with
Neo4j. It does not lock the Neo4j database itself. The guard uses OS file locks;
NFS is unsupported.

The process that serves holds the lock, so `visp-memory serve --reload` works: the
reloader releases its import role, and the child retains its role from import
through lifespan startup. `serve` without reload also retains the import role.
Unidentified ASGI importers still release at the end of import and claim again
at startup; retaining a role there could block a supervisor's serving child.
One server process owns a store. `uvicorn --workers N` with N above 1 is not supported: the
first worker serves and refused Uvicorn workers log once and stay idle without
serving, while continuing to answer supervisor heartbeats. This prevents a
respawn loop without acting on the supervisor. Idle workers do not take over
if the serving worker exits; stop the supervisor and restart with a single worker.
Unrelated servers still fail startup normally.

Every record can carry a `written_by` label of the form `{agent, session, client}`
on memories and evidence (in `metadata`) and on intents (in `context`); see the
[contract](../reference/CONTRACTS.md#written_by-contract). It is supplied by the
client and can be forged. It grants no trust or authority and does not change
provenance, but the session label is a relevance factor in proactive recall: a
memory whose `written_by.session` equals the query's session ID gets a "matched
session" ranking boost.

| Source | How the label is set |
|---|---|
| REST clients | `X-Visp-Agent`, `X-Visp-Session` and `X-Visp-Client` request headers |
| Local CLI, hooks | `VISP_MEMORY_AGENT` and `VISP_MEMORY_SESSION` in the environment; the Claude Code hooks use agent `claude-code` and Claude's session ID |
| stdio MCP | Agent from `VISP_MEMORY_AGENT`, else the MCP client's name; session from a valid `VISP_MEMORY_SESSION`, else a per-process ID; client `name/version` |
| HTTP MCP | Request headers; agent defaults to the authenticated principal's username |

Client mode (`RemoteStorage`) sends these as the headers above. REST requests use
only the supplied valid labels: missing or invalid headers never fall back to the
server's environment. Caller-supplied `written_by` in metadata or intent context
is ignored; updates preserve the original writer. Privileged imports retain the
historical attribution carried by the export. Invalid labels are dropped, and
stdio MCP uses a stable per-process ID when its session environment value is invalid.

## Environment variables

| Variable | Purpose |
|---|---|
| `VISP_MEMORY_CONFIG` | Explicit config file path. Startup fails if the file is missing. `serve --shared` sets it |
| `VISP_MEMORY_SERVER_MAX_IMPORT_BODY_BYTES` | Maximum REST graph import body in bytes; default 67108864 (64 MiB). Oversized bodies return 413 before JSON parsing, including streamed requests. Also configurable as `server.max_import_body_bytes` |
| `VISP_MEMORY_SERVER_SHARED` | Marks the server project-neutral (no server-wide `repo_id`). `serve --shared` sets it |
| `VISP_MEMORY_SERVER_LOCAL_OWNER_MODE` | Open local-owner mode. `serve` sets it only for a credential-free loopback bind; see [authentication](AUTHENTICATION.md#open-local-owner-mode) |
| `VISP_MEMORY_AGENT`, `VISP_MEMORY_SESSION` | Writer labels described above |
| `VISP_MEMORY_MCP_ALLOW_REPO_OVERRIDE` | Lets a stdio MCP call name a different `repo_id` than its configured one. Accepts `1`, `true`, `yes` or `on`, in any letter case |

## Maintenance and boundaries

Local maintenance uses a random owner token stored with owner-only file
permissions under `~/.visp-memory/run`. This proves access by the same OS user
through that file, not a separate application identity. The token exists only in
open local-owner mode; see [authentication](AUTHENTICATION.md#owner-maintenance-on-a-local-server)
for what it unlocks. The dashboard cannot read the token; dashboard maintenance
requires an account.

Runtime proof and discovery files are keyed by the literal bind host and port
(`owner-<host>-<port>.token` and `server-<host>-<port>.json`; IPv6 colons are percent
encoded). Discovery also records `bind_host`. A client sends proof only to that
literal host: a `localhost` client URL cannot use proof for `127.0.0.1` or `::1`.
Legacy per-port files work only when their discovery URL records the same literal
host and port. A token file alone is insufficient. Startup refuses to replace a
record belonging to a live or unidentified process, even for another data directory;
shutdown removes only its own proof and record.

Stdio MCP also refuses a `repo_id` override unless
`VISP_MEMORY_MCP_ALLOW_REPO_OVERRIDE` is enabled. That stops an assistant naming
the wrong project by mistake; it is not access control. For credentials on a server that is not in open local mode,
set `VISP_MEMORY_API_KEY`, `VISP_MEMORY_JWT_TOKEN` or `storage.api_key` in the
client; [authentication](AUTHENTICATION.md) describes the server side and
[storage](STORAGE.md) covers storage behavior.

## Trust tier of client writes

Client-mode writes with valid local-owner proof are stored as `assisted`, the
same tier and source as local stdio MCP writes. They are eligible for automatic
context subject to the normal trust, relevance, scope, and freshness checks.
`RemoteStorage` sends `X-Visp-Owner-Token` automatically to its own loopback
server when it can read that server's owner token file.

The server selects the distinct `local_owner` write channel only for the
anonymous local-owner path: local-owner mode must be enabled, the peer must be
loopback, and the token must match with a constant-time comparison. Recording
`metadata.write_channel: local_owner` keeps these writes auditable separately
from stdio MCP. Payload tags, source, attribution, or flags cannot select a tier.

This applies to memory and evidence creation, semantic revisions, and intent
outcomes (`complete`, `close`, `reopen`, and `outcomes`). Workflow-status reports
also record `local_owner`; they remain reports from the external workflow.
Intent creation and context updates do not assign a trust channel. Existing
records are not relabelled.

Accounts, PATs, JWTs, API keys, anonymous callers without a valid token, and
non-loopback peers retain the `external` tier, even with a token header. Dashboard
writes therefore remain external. See [trust](../reference/TRUST.md) for the
injection policy.
