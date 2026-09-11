# Trust and privacy

Stored memory can be stale, incorrect, or contain instructions planted by another
source. Visp Memory records provenance and limits what enters automatic prompt
context. Retrieved content remains information to inspect, not authority to act.

## Stored data

The configured data directory holds memory content, evidence, categories, tags,
metadata, relationships, intents, capture records, and usage history. Servers
also store accounts, sessions, and operational journals. Treat backups and exports
as project data with the same access restrictions as the original store.

Evidence is immutable source content with a hash and provenance. Semantic beliefs
cite separate, same-project evidence; missing or cross-project citations refuse
the write. See [storage contracts](development/CONTRACT_SURFACE.md#evidence-and-belief-storage-contract).

## Provenance and quarantine

The package assigns provenance from the write channel. A memory payload cannot
make itself trusted by claiming an author or adding a provenance tag.

| Tier | Source channel | Eligible for automatic context? |
|---|---|---|
| `authored` | Local CLI writes | Yes, subject to trust and relevance checks |
| `derived` | Package Git/test capture, bootstrap, and workflow adapters | Yes, subject to checks |
| `assisted` | MCP, conversation capture, compression, or reflection | Yes, subject to checks |
| `unknown` | Direct library writes, missing or malformed provenance | No; quarantined |
| `external` | HTTP/REST, imports, and instruction-file ingestion | No; quarantined |

Trust decays with age, with bounded reinforcement from positive use. Repeated
exposure alone does not keep a stale memory permanently eligible. Quarantine and
trust decay affect prompt eligibility; they do not delete the record.

Explicit recall can show quarantined records for inspection. Both explicit and
guarded reads still require valid project, time, environment, and task scope.
Graph expansion cannot use rejected nodes as hidden bridges. An attacker with
direct database-mutation access is outside this policy boundary.

### Notes saved in the dashboard or API

Dashboard writes use HTTP, including writes by signed-in administrators. They
remain searchable in Recall but are excluded from task briefs and automatic
context. Setting a pending note to active records a review; it does not change
the note's provenance or make it eligible for automatic context.

To use a conclusion you have personally checked, inspect the original source,
then record your own reviewed conclusion through the local CLI in the **same
project and data store**, including a source reference:

```bash
visp-memory record "Verified: session cookies use HttpOnly; source: src/auth.py" --repo my-project
```

For a Docker-hosted store, run that CLI command inside the application container
using `docker exec`; a host CLI with a different data directory writes to a
different store. The original HTTP note keeps its provenance. Do not blindly
copy external instructions or relabel an external record as trusted. A CLI note
still has to pass relevance, scope, freshness, and other eligibility checks.

Inspect your store with:

```bash
visp-memory audit
visp-memory audit --quarantined
visp-memory audit --stale
```

The [benchmark report](BENCHMARK.md#poisoning-resistance) includes both attack
retrieval and useful-answer results on a synthetic corpus. It is not a guarantee
against arbitrary prompt injection.

## Secrets risk and safe capture

Normal storage writes scan for known credential patterns and redact matches before
persisting them. Redacted records carry quality flags such as `secret_redacted`.
Imports and migrations refuse content that would require rewriting, preserving
hash and historical-integrity guarantees.

Detection is a safety net, not a guarantee. Record the engineering fact rather
than raw prompts, responses, credentials, or customer data. Preview conversation
and instruction capture when sources may contain sensitive content, and inspect
exports before sharing them.

## Provider boundaries

The retrieval policy and dreaming cycles do not call an LLM. Optional embeddings
and configured model-based features are separate provider boundaries.

- `noop` uses local keyword search without embeddings.
- A local transformer model processes text locally after any required model download.
- Ollama processes text at its configured endpoint, which may be another machine.
- Cloud embeddings and optional cloud model features may send content or queries to
  the selected provider. Choose configuration consistent with the project's data policy.

See [storage and embeddings](development/STORAGE.md#how-embeddings-work) before enabling a provider.

## Local and server modes

Local CLI and stdio MCP use the machine's filesystem and project configuration.
Server clients rely on authenticated API access and the server's project scopes.
Do not expose a local installation beyond localhost without configuring access
controls and HTTPS.

### Auth defaults and team access

Authentication is enabled by default; no public default account password is
shipped. Prefer scoped tokens for integrations and limit them to the necessary
projects and operations. [Accounts and tokens](deployment/AUTH.md) owns setup and
compatibility settings. Team and cross-project features remain
[frozen](FEATURE_STATUS.md), with access checks still required.

## Workflow authority and compatibility

Memory describes goals and records outcome history. Explicit
[workflow reports](development/WORKFLOW_REPORTS.md) can mirror the owning
assistant's status, with reporter identity, revisions, and evidence. Memory does
not execute those checks or certify completion, readiness, permission, or scope.

Generic completion commands remain advisory. The legacy
`llm.intent_auto_complete`, `VISP_MEMORY_LLM_INTENT_AUTO_COMPLETE`, and
`allow_auto_complete` inputs are accepted but ineffective; the evaluator exposes
deprecated inputs and returns `authoritative: false` and `status_changed: false`.

## Retention and deletion

Archiving, merging, and quarantine retain data; they are not erasure. Dreaming
keeps original content and action history so changes can be undone, and never
purges memories. Deleting an active memory is not a promise that evidence,
operational history, prior exports, or backups have been erased.

Choose retention for the whole data root and its backups. For restoration and
migration, follow [storage maintenance](development/STORAGE.md#backup-and-schema-upgrades).
