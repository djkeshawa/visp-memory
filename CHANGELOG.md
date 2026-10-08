# Changelog

Notable changes to visp-memory. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Releases up to 0.7.10 are
described only by their
[GitHub release notes](https://github.com/djkeshawa/visp-memory/releases).

The release workflow publishes the section matching the tag (or `Unreleased`, if the
tag has no section yet) as the GitHub release body. See
[Releasing](docs/development/RELEASING.md#release-notes).

## [Unreleased]

## [0.8.1] - 2026-10-09

Fixes from a codebase audit (#82), chiefly team visibility on a shared server, plus
dashboard dependency updates for published advisories (#83, #84).

### Upgrading

- Environment overrides are validated against the field they set. A value the field
  rejects now stops configuration loading with an error instead of being coerced: for
  example `VISP_MEMORY_SERVER_AUTH_ENABLED=tru` used to turn authentication off, and
  out-of-range numbers such as `VISP_MEMORY_STORAGE_CONNECT_TIMEOUT_SECONDS=-1` were
  accepted.
- Manual merges refuse memories whose provenance tier, team, environment or task
  scope, or validity bounds differ, even when the content is identical.
- HTTP MCP refuses a `repo_id` with leading or trailing whitespace. Such an ID was
  authorized as written but read and written with the whitespace removed, which could
  reach a different project.

### Fixed

- JUnit capture records every failed or errored test case, even when a suite's
  `failures` and `errors` counters are missing or wrong.
- `GET /remember` finds the latest memory the caller can see even when more than 50
  newer memories are hidden from them.
- Context, recall, task briefs and proactive recall with `as_of` judge trust decay at
  the requested time rather than now.
- A stricter configured `min_trust` is honoured when memories are selected for
  injection.
- Memory-intelligence reports normalize timestamps with UTC offsets before comparing
  ages.
- `POST /memories/merge` answers 400 instead of failing when the merge group is
  invalid.
- The Neo4j and ArcadeDB backends can read a memory without counting an access.
  `GET /memories/{id}/peek` and recall-utility calls naming a memory no longer fail
  on them, and visibility checks no longer raise the access count of records the
  caller cannot see.

### Security

- HTTP MCP tools and resources apply the authenticated user's team visibility to
  memories, intents, relationships, evidence, statistics and recall utility within the
  requested project. Memories and intents written over HTTP MCP record the caller as
  author and carry the caller's team.
- REST graph recall applies team visibility to seed memories and traversal, task
  briefs only match intents the caller can see, and recall-utility inspect, verify and
  reset aggregate only visible memories for non-administrators.
- `POST /memories/merge` can no longer raise a memory's trust tier: merging records
  from different provenance tiers used to carry the other records' `provenance:*`
  tags onto the canonical memory.
- Evidence captured automatically with a memory keeps the memory's author and team.
- Private keys are redacted through their `END` line, or to the end of the text when
  a capture is truncated, instead of only the `BEGIN` header.
- Memory exports redact `bootstrap_admin_password`.
- Dashboard build dependencies: `sharp` 0.35.5 (GHSA-wq5f-xc86-pv6w, librsvg),
  `source-map-js` 1.2.2 (GHSA-68fv-2mgg-jv7q) and `next` 16.3.8 (GHSA-3w37-wq28-93x7,
  GHSA-4jqv-mc3x-m676, GHSA-39w2-rjm5-chcv, GHSA-f87g-xv8r-7p7x, GHSA-mcj8-r9mp-w47p,
  GHSA-cjq9-62q9-8jv4).

## [0.8.0] - 2026-10-04

Everything merged since 0.7.10 (#49 to #78). The headline is one local server that
many projects and agents share; see [Shared server](https://github.com/djkeshawa/visp-memory/blob/develop/docs/guides/SHARED_SERVER.md).

### Upgrading

Read this before upgrading a machine that runs `visp-memory serve`, uses personal
access tokens, or has Codex configured.

- **Personal access token scopes are stricter** (the full table is
  `src/visp_memory/server/pat_scopes.py`):
  - `GET /repos/{id}/export` needs `project:read`, `memory:read` and `intent:read`.
  - `POST /turn-keys/search` needs `memory:read`.
  - `GET /remember`, `GET /reports/memory-intelligence`,
    `GET /reports/memory-intelligence/text`, and `GET /repos/{id}/context` now
    need `memory:read`; `project:read` alone returns 403.
  - `POST /repos/{id}/import` is refused to every token, including `*` and admin
    tokens. Import with the local owner token or a dashboard administrator session.
  - `/maintenance/*` needs an admin token.
  - Account and token routes under `/auth` refuse tokens; use a dashboard session.
    `GET /auth/me` still works with a token.
  - `GET /diagnostics/capabilities` is readable with `project:read`.
  - Writes (POST/PUT/PATCH/DELETE) on routes that used one scope for every method now
    need a write scope or admin; for example `PATCH /intents/usage` needs
    `intent:write`.

  Re-issue tokens that lack the scopes your clients now need.
- **Open local-owner mode checks `Host` and `Origin`.** This is the mode plain
  `visp-memory serve` and `serve --shared` enter on a loopback bind with no
  credentials. A `Host` other than `localhost`, `127.0.0.1` or `[::1]` gets HTTP 421,
  and a cross-origin POST/PUT/PATCH/DELETE from a page that is not this server's own
  or a configured CORS origin gets 403. Opening the dashboard as
  `http://0.0.0.0:PORT` or by the machine's hostname no longer works; use
  `127.0.0.1` or `localhost`. A reverse proxy in front of such a server must forward
  a loopback `Host`.
- **One writer per served data directory.** While a server owns a data directory,
  local-mode CLI, MCP and hook processes on that directory are refused with a
  message naming the server. Switch those projects to client mode with
  `visp-memory connect` (or `storage.mode: client` and `storage.server_url` in
  `visp-memory.yaml`). A server refuses to start while local writers hold the
  directory; close them or switch them to client mode first. `uvicorn --workers`
  above 1 is unsupported: one worker serves and the extra workers stay idle without
  serving (so uvicorn stops respawning them), so run one worker. `storage backup|upgrade --offline` now
  actually checks that no server holds the directory. `VISP_MEMORY_STORAGE_WRITER_GUARD=off`
  disables the guard.
- **Workflow status needs the owner token.** An anonymous loopback caller of
  `POST /intents/{id}/workflow-status` must send `X-Visp-Owner-Token`;
  `RemoteStorage` does this automatically for a loopback server. Authenticated
  accounts are unaffected.
- **Shared servers refuse unscoped requests** with 400 `repo_id is required`. Give
  every client a `repo_id` (`visp-memory connect` writes one).
- **`connect --migrate-local` imports memories and Evidence as `external`.**
  The client replaces memory provenance tags and sources, clears prior approvals,
  and downgrades Evidence provenance before sending. Source paths and original
  tier counts are printed; `--yes` is no longer required. Content, scope and
  Evidence hashes stay intact, preserving valid signed authority attestations.
  After owner review, re-approve memories with `PATCH /memories/{id}` by replacing
  provenance tags with `provenance:authored` and setting `source: authored`
  (administrator or owner token only).
  Other import callers retain their existing behavior; the local store is untouched.
- **Codex:** `hooks install codex` now writes a `~/.codex/config.toml` block that no
  longer pins `cwd`, `VISP_MEMORY_REPO_ID`, `VISP_MEMORY_STORAGE_MODE` or
  `VISP_MEMORY_STORAGE_SERVER_URL`; each project's settings come from its
  `visp-memory.yaml`. An old pinned block is replaced, and the previous file is kept
  as `config.toml.backup`. `hooks install codex --server-url` is now ignored; use
  `visp-memory connect`.
- **Client-mode writes with validated local-owner proof are now `assisted`**,
  matching local stdio MCP and eligible for automatic context subject to normal
  checks. The server records the distinct `local_owner` channel for memory and
  evidence creation, semantic revisions, and intent outcomes; workflow-status
  reports also record that channel. `RemoteStorage` supplies the owner token to
  its own loopback server automatically. Accounts, PATs, JWTs, API keys, missing
  or invalid proof, and non-loopback peers remain `external`. Existing records
  retain their provenance.
- **REST revisions take the caller's tier.** A semantic revision made over REST
  used to inherit the original belief's tags and source, so an `external` caller
  could produce a successor at the original's higher tier. The successor now gets
  the revising request's channel (`assisted` with owner proof, otherwise
  `external`). Local revisions are unchanged.
- **Relative `data_dir` values from a found config file are resolved**, so symlinks
  and `..` in the path are resolved to the real directory.
- **Review statuses over REST.** `POST /memories` and `PATCH /memories/{id}` accept
  `status` values `quarantined` and `rejected`. `POST /recall` still refuses both.
- In client mode, unscoped reads such as `get_stats()` now return the configured
  project's data, not the server-wide view, matching local mode.
- **Graph imports are limited to 64 MiB** by default; larger bodies get HTTP 413.
  Raise it with `server.max_import_body_bytes` or
  `VISP_MEMORY_SERVER_MAX_IMPORT_BODY_BYTES`.
- **Owner-token and discovery files are named by host and port**:
  `~/.visp-memory/run/owner-<host>-<port>.token` and `server-<host>-<port>.json`
  (IPv6 colons percent-encoded). The client sends the token only to that exact
  origin. Records written by older servers (`server-<port>.json`) are still read.
- **A second `serve` on a port whose record belongs to a live server refuses to
  start** instead of overwriting that server's token and discovery files.
- **`connect --migrate-local` refuses** when the local store's records belong to a
  different repository ID than the target, naming both IDs.

### Added

- `visp-memory serve --shared [--data-dir PATH]` serves one project-neutral store at
  `~/.visp-memory`, with its own pinned `config.yaml`. It refuses a shared config that
  sets `repo_id` and ignores `VISP_MEMORY_REPO_ID`. `--data-dir` without `--shared` is
  refused.
- `visp-memory connect [--server-url] [--repo] [--migrate-local] [--agent-config claude-code|codex]`
  points a project at a running server: it discovers the server from
  `~/.visp-memory/run/server-<port>.json`, writes `repo_id`, `storage.mode: client`
  and `server_url` into `visp-memory.yaml`, checks health and registers the repository.
  `--migrate-local` copies the old local store through the server and leaves the old
  directory untouched. It edits the config file the project already uses (JSON in
  place, simple YAML with comments kept, otherwise a full rewrite after a `.backup`
  copy) and never creates a `visp-memory.yaml` that shadows another config file.
  Discovered servers must be HTTP(S) loopback origins, and API keys or JWTs are not
  sent to a discovered URL that differs from the configured one.
- `hooks install claude-code --mcp` merges a `visp-memory` server into the project
  `.mcp.json` without touching other servers.
- Client-mode parity over REST: recall-utility feedback (`/recall-events`),
  `/memories/{id}/peek`, `/turn-keys/search`, `/intents/usage`,
  `/repos/{id}/registration`, `/diagnostics/capabilities`, repository export and
  import (`/repos/{id}/export|import`), `/memories/{id}/attestation`, embedding-index
  inspect and rebuild, audit-log listing and purge. `RemoteStorage` reports the
  server's capabilities and falls back to the old static set on an older server.
- Agent attribution: every write records `metadata.written_by` (`agent`, `session`,
  `client`). Sources are `VISP_MEMORY_AGENT` / `VISP_MEMORY_SESSION`, MCP
  `clientInfo`, the Claude Code hook's `session_id`, and the `X-Visp-Agent`,
  `X-Visp-Session` and `X-Visp-Client` headers over REST. It is a label: it does not
  affect trust tier, visibility or authorization, but an explicit `session_id` in
  recall matches the writer's session. Imports keep the exported value. The Codex
  plugin sets `VISP_MEMORY_AGENT=codex`. Experimental.
- Owner-token maintenance in open local-owner mode: the server writes a `0600`
  token to `~/.visp-memory/run/owner-<host>-<port>.token`, and a loopback caller sending it
  as `X-Visp-Owner-Token` may purge, archive, restore, run retention and
  consistency checks, reindex, run dreaming, import and read the audit log.
  Accounts, teams, providers and routing stay admin-only.
- Single-writer guard (`core/writer_lock.py`) for SQLite and ArcadeDB data
  directories, using OS file locks that are released on exit or crash.
- Environment variables: `VISP_MEMORY_CONFIG` (pin the config file; a relative
  `data_dir` resolves against it), `VISP_MEMORY_SERVER_SHARED`, `VISP_MEMORY_AGENT`,
  `VISP_MEMORY_SESSION`, `VISP_MEMORY_MCP_ALLOW_REPO_OVERRIDE` (let a stdio MCP tool
  name a repo other than the project's pinned one) and
  `VISP_MEMORY_STORAGE_WRITER_GUARD`.
- `EpisodicMemory.record(..., status=...)`, keyword-only, default `active`.
- Documentation: [Shared server](https://github.com/djkeshawa/visp-memory/blob/develop/docs/guides/SHARED_SERVER.md). Feature status: shared
  local server and writer guard Beta, `written_by` Experimental.

### Changed

- Client-mode MCP reports server failures as errors: `server_unavailable` (names the
  URL and suggests `visp-memory serve --shared`) and `server_rejected` (includes the
  server's `detail` and status code), with one log line and no traceback. Other
  failures keep the generic `request_failed`.
- `RemoteStorage` resends a request refused with 403 only when the owner token on
  disk has changed or been removed.
- `contract propose` stores the proposal as `quarantined` in one write, so an
  unreviewed proposal is never active.
- A writer conflict prints one line of guidance and exits 1 instead of a traceback;
  `contract recall` and `contract propose` keep their JSON envelope.
- Environment overrides are coerced by the config field's type from one table.
- `server-<host>-<port>.json` records the server's bind host and `ppid`.
- Repository IDs containing `/` work on `/repos/{id}/export`, `/import` and
  `/registration`; the client URL-encodes them.
- Export, import, recall-utility and inspection handlers run in the threadpool
  instead of blocking the event loop.
- Against an older server, a missing route is told apart from a missing record:
  `peek` falls back to a normal read, turn-key search returns nothing, and the legacy
  capability fallback is retried rather than cached for the process lifetime.
- Client-mode `visp-memory doctor` reads repository registration and intent usage
  from the server.
- The serving process keeps the server role from import into startup, closing the
  gap between them; with the Neo4j backend the role now also covers `auth.db` and
  `lifecycle.db` in the data directory.
- CI runs the real `visp-memory serve --shared` quick start on Ubuntu and Windows.

### Fixed

- `click` is now a declared dependency. A fresh install of 0.7.10 with typer 0.27 or
  later failed on every command with `No module named 'click'`.
- `VISP_MEMORY_SERVER_LOCAL_OWNER_MODE=false` enabled local-owner mode, because the
  value was not coerced to a boolean.
- In client mode, `contract propose` left an active proposal behind and reported
  failure, and `review list|accept|reject` failed.
- In client mode against a shared server, `memory_list_intents`,
  `memory_list_warnings`, `memory_compress`, `memory_decay`, `memory_clear_goals`,
  `visp-memory compress` and `decay` failed because calls were sent unscoped.
  Client-mode recall-utility feedback, the supersede path, turn-key search and
  capability reporting were also broken.
- Idempotent retries of evidence, and replays of signed prohibitions, are no longer
  rejected when a different agent or session retries; metadata is compared as the
  JSON the store holds, ignoring `written_by`.
- `serve --reload` works with the writer guard: the serving process, not the
  uvicorn supervisor, holds the server role.
- A false conflict between two local writers, seen on loaded machines, no longer
  occurs.
- A stale `.locks/server.json` left by a crashed server no longer blocks
  `connect --migrate-local`; a store counts as served only while its lock is held.
- An idle extra uvicorn worker stops on Windows when its supervisor shuts down, and
  exits if the supervisor is gone.
- HTTP MCP works under `--root-path`.
- A REST request without attribution headers no longer takes the server process's
  own `VISP_MEMORY_AGENT` / `VISP_MEMORY_SESSION`; an invalid `VISP_MEMORY_SESSION`
  falls back to a per-process session instead of dropping the label.
- In client mode, `review accept|reject` honour `--repo`, and the `--endpoint` help
  no longer claims the contract commands contact nothing.
- A regression in how a relative `data_dir` was resolved for
  `.visp-memory/config.yaml` opened an empty store at
  `<project>/.visp-memory/.visp-memory/data`. It existed only on `develop` and never
  shipped; 0.7.10 behaviour is kept.

### Security

- `PATCH /memories/{id}` can no longer change a memory's trust tier for ordinary
  writers: `provenance:*` tags and `source` keep their stored values unless the caller
  is an administrator or holds the local owner token. Before this, any principal with
  write access could promote a record to `authored` and have it auto-injected. This
  was also true in 0.7.10.
- DNS-rebinding guard for open local-owner mode: `Host` and `Origin` checks, described
  under Upgrading.
- Personal access token scopes come from one ordered table, and a test fails for any
  route that falls through to the default. This closed a gap where new routes such
  as export and turn-key search defaulted to `project:read`.
- Token scopes are matched on the routed path, so a `--root-path` or prefix proxy no
  longer lets a `project:read` token reach unmatched routes such as token minting.
- Anonymous workflow-status reports need the owner token.
- `~/.visp-memory`, its data directory and `run/` are created, or tightened, to
  `0700` on POSIX.
- `POST /memories` strips a caller-supplied `metadata.team_id`, which drives
  visibility.
- Caller-supplied `written_by` is stripped from intent context and other REST
  metadata on create and kept from the original on update, so a forged session label
  cannot boost recall.
- `connect --migrate-local` imports at the `external` tier (see Upgrading), so a
  crafted local store cannot import high-trust records.
- A stdio MCP tool naming another repository is refused unless
  `VISP_MEMORY_MCP_ALLOW_REPO_OVERRIDE` is set, and unscoped writes are checked
  before dispatch.

[Unreleased]: https://github.com/djkeshawa/visp-memory/compare/v0.8.1...develop
[0.8.1]: https://github.com/djkeshawa/visp-memory/compare/v0.8.0...v0.8.1
[0.8.0]: https://github.com/djkeshawa/visp-memory/compare/v0.7.10...v0.8.0
