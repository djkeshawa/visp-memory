# Memory Governance

This document describes how Visp Memory should be operated when project memory
contains engineering decisions, team context, or captured development activity.

## Stored Data

Visp Memory stores memory content, categories, importance, tags, metadata,
relationships, active intents, repository IDs, team/user records, recall utility
events, and capture manifests. Local mode stores data under the configured
storage directory. Server mode stores data in the configured backend and exposes
it through authenticated API routes.

## Workflow Authority And Compatibility

Visp Memory records intent descriptions and cited, non-authoritative outcome
history. It does not decide or change intent completion, closure, reopening,
permission, scope, or readiness. Completion-language evaluation is deterministic,
advisory, non-mutating, and returns `authoritative: false` and
`status_changed: false`; it never returns `decision="completed"`.

For one compatibility cycle, these legacy inputs remain accepted but have no
effect:

- `llm.intent_auto_complete` in configuration;
- `VISP_MEMORY_LLM_INTENT_AUTO_COMPLETE` in the environment; and
- `allow_auto_complete` in the intent-evaluation REST request.

The evaluator response exposes both deprecated input names under
`deprecated_inputs`. Existing historical `active`, `completed`, and `closed`
rows remain readable. Legacy REST, CLI, MCP, and library completion surfaces
append provenance-bearing outcome history where an actor/channel is available,
but the stored intent status is unchanged. Direct backend status updates and
`complete_intent` calls are ineffective; mixed backend updates still apply only
non-status fields.

## Secrets Risk

Memory content can accidentally include secrets from logs, prompts, responses,
stack traces, environment variables, source files, or captured conversations.
Do not record API keys, credentials, tokens, private prompts, full model
responses, personal data, or unrelated repository/team memory. Prefer summaries
that describe the engineering fact without copying sensitive text.

**This is enforced, not only advised.** Every write funnels through one choke point in
each storage backend's `store_memory`, which scans content for known credential formats
(cloud keys, provider tokens, private keys, JWTs, connection-string passwords, bearer
tokens, and credential assignments) and rewrites them before anything is persisted:

    "deploy fails unless AWS_ACCESS_KEY_ID=AKIA..." →
    "deploy fails unless AWS_ACCESS_KEY_ID=[REDACTED:aws-access-key-id]"

The surrounding engineering fact is kept — rejecting the whole memory would lose the
part worth having. Affected memories carry `secret_redacted` and
`secret_redacted:<kind>` quality flags, and `visp-memory audit` reports the count.

Detection is biased towards precision: placeholders (`changeme`, `<your-key>`,
`${VAULT_TOKEN}`), environment indirection (`os.getenv(...)`), and prose about secrets
are deliberately left untouched, because silently corrupting a legitimate memory is a
worse failure than missing one. It is a safety net for accidental capture, **not** a
guarantee — do not deliberately store secrets and rely on it.

## Provenance Ownership And Quarantine

Write provenance is owned by package adapters. Caller tags, import fields, HTTP request
fields, and memory content cannot select a more trusted tier. The durable episodic and
semantic layers replace payload provenance at their write choke points; channel-specific
capture and import paths apply the same central policy before storage.

| Write path | Provenance | Injection policy |
|---|---|---|
| Direct public library call | `unknown` | Quarantined |
| CLI write command | `authored` | Eligible subject to trust and decay |
| MCP, conversation capture, compression, or reflection | `assisted` | Eligible subject to trust and decay |
| Git/test capture, bootstrap, Kit outcome contract | `derived` | Eligible subject to trust and decay |
| HTTP/REST, import, instruction ingestion | `external` | Quarantined |
| Missing or malformed provenance | `unknown` | Quarantined |

`_write_channel` is an internal adapter argument, not an end-user provenance API. Only
package-owned adapters pass it, unknown values are rejected, and the mapping itself is
read-only. HTTP and import payloads may carry ordinary tags, but self-claimed provenance
is removed. Raw storage calls that provide no provenance remain unlabelled and therefore
assess as `unknown`; explicit recall still exposes quarantined records.

### Read-time enforcement

Trust is checked again before any guarded result is returned to prompt-adjacent callers
or formatted into prompt content. Task briefs filter before token selection, and graph
relationships are filtered before traversal. The guarded surfaces are full context,
targeted `relevant_for` groups, MCP SessionStart, task briefs, proactive
file/error/directory recall, graph traversal, and related-memory HTTP output. Graph
traversal removes relationships touching a rejected node, so quarantine cannot be used
as an invisible path between trusted endpoints.

Structured diagnostics report considered, allowed, quarantined, and below-threshold
counts plus stable per-memory reasons. They are additive to existing result shapes. A
direct explicit recall remains an inspection operation and may return quarantined data;
callers must not copy such results into prompts without applying the unsolicited filter.

## Provider Boundaries

Embedding and LLM providers are configuration boundaries. `noop` and local
providers keep recall local. Cloud embedding or LLM providers may send memory
content or queries to that provider. Do not enable a cloud provider for private
projects unless the provider, retention policy, and data handling expectations
are acceptable for the team.

## Local And Server Modes

Local mode is appropriate for one developer and local-first workflows. Server
mode is appropriate for shared dashboards, MCP clients, and team memory. In
server mode, set explicit authentication and repository scoping before exposing
the API beyond localhost.

## Retention And Deletion

Use `visp-memory list`, `visp-memory recall`, and dashboard views to inspect
stored memory. Use deletion, archiving, decay, and utility reset workflows to
remove stale or unsafe data. Export/import may carry capture manifests and
memory metadata, so exported files must be treated as sensitive project data.

## Auth Defaults

Server deployments should configure `VISP_MEMORY_JWT_SECRET`, API keys, and
anonymous-access policy explicitly. Local development may disable auth for
localhost-only smoke tests, but release and team deployments must keep auth and
scoped-access tests in the release gate.

## Team Access

Team and repository records should be scoped so non-admin users can only read
their own user context and authorized team/repository memory. Cross-team and
cross-repository recall should be deliberate, documented, and tested.

## Safe Capture

Capture workflows for git, test reports, and conversations should avoid raw
secret material. Review captured summaries before sharing exports. Conversation
capture should be dry-run first when logs may contain prompts, responses,
customer data, credentials, or unrelated repositories.

## Release Gate

Security and scoped-access behavior remain release-gate checks:

```bash
python3 -m pytest tests/server/test_auth.py tests/server/test_collaboration.py
```

Run these with the broader release checks whenever server auth, team access,
repository scoping, capture, or assistant integration changes.
