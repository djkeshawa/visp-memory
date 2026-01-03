# Memory Governance

This document describes how Visp Memory should be operated when project memory
contains engineering decisions, team context, or captured development activity.

## Stored Data

Visp Memory stores memory content, categories, importance, tags, metadata,
relationships, active intents, repository IDs, team/user records, recall utility
events, and capture manifests. Local mode stores data under the configured
storage directory. Server mode stores data in the configured backend and exposes
it through authenticated API routes.

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
