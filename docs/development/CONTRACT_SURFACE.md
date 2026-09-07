# Machine and storage contracts

`visp-memory contract ...` is the versioned surface a coordinator speaks. Every
command in it prints one JSON object on stdout, always carrying `contractVersion`
and `success`, and reports failure as a `success: false` envelope rather than a
traceback.

The CLI contract and the Evidence storage contract below are separate boundaries.
Neither grants workflow authority. Human-facing commands are not implicitly part
of the versioned machine surface.

## What is on the contract

| Command | Returns | Notes |
|---|---|---|
| `contract recall QUERY` | Ranked `entries` with scores | Accepts `--task`, `--file`, `--symbol`, `--constraint`, `--session`, `--environment`, `--task-type`, `--as-of`, `--min-score`, `--repo`. Naming files or symbols can only *add* structurally-reached memories, never remove or reorder the text results, and adds a `retrieval` block describing what the structure channel did — including when it did nothing, and why. Without files or symbols, the existing text-recall envelope is preserved. |
| `contract propose CONTENT` | `proposalId`, `status: quarantined`, `durable: false`, and the exact `acceptCommand` / `rejectCommand` | A proposal is **not** retrievable by `contract recall` until a human accepts it. |

`--endpoint` is accepted on both and ignored: it exists so a coordinator's argument
list stays stable. Nothing on this surface opens a network connection.

## What is not on the contract

The following operations exist outside the versioned `contract` command group.
Some have JSON options, but that does not make them contract envelopes. Integrators
must choose the actual interface rather than assume a `contract` equivalent.

| Operation | Existing surface | Integration limit |
|---|---|---|
| Cited task brief | `visp-memory brief --format json`; MCP `memory_prepare_task` | `contract recall` returns ranked entries rather than the structured brief |
| Proposal review | `visp-memory review list\|accept\|reject` | Proposal responses name the review commands; review itself is outside the JSON contract |
| Recall feedback | `visp-memory feedback log`; MCP `memory_feedback_log` | Contract-only clients cannot log positive use through this surface, so recalled memories receive no use reinforcement from those clients |
| Injection explanations | `visp-memory preview --json` | Explains selected records and available rejection counts; not a contract recall explanation envelope |
| Error precedent | `visp-memory find-error` | No dedicated contract operation |
| Provenance and eligibility | `visp-memory audit --json` | Inspection is outside the contract command group |
| Conflict checking | `visp-memory quality conflicts` | No contract pre-write conflict-check operation |

## Deliberately not on the contract

- **Direct writes** (`record`, `decision`, `learn`, `warn`). A coordinator proposes;
  a human accepts. `contract propose` is the only write, and it quarantines. This is
  the design, not an oversight.
- **Maintenance** (`decay`, `compress`, `dedup`, `import`, `export`, `serve`, `init`,
  `hooks install`). Operator actions, not agent actions.
- **Frozen areas** (`teams`, `admin`, `repos`) — see
  [FEATURE_STATUS.md](../FEATURE_STATUS.md).

## Rule for anything added here

A new command must report only capabilities the package implements. Test a
returned command or lifecycle promise by executing it and verifying the outcome;
matching its wording alone does not establish that the integration works.

## Evidence and belief storage contract

This contract defines the storage boundary introduced by schema version 3.
Each backend must either implement a contract operation exactly or advertise it
as unsupported and fail closed.

### Record types

Evidence is an immutable, repository-scoped source record. It contains a stable
ID, exact content, a full SHA-256 content hash, an evidence type, provenance,
metadata, and creation time. Evidence content, hash, repository, type, and
provenance are append-only. An attempted evidence update is refused; correcting
evidence creates a new record.

Direct Evidence writes apply the storage secret-redaction policy before content
is hashed or persisted. The stored hash therefore always describes the redacted
content. Stable format-2 imports and schema-v2 migration history are different:
they are never silently rewritten. If their Evidence or legacy content would
require redaction, the entire operation fails before vector, database, marker,
backup, or destination-file writes. Current stores with secret-bearing or
hash-invalid Evidence also fail closed rather than returning or exporting it.

A semantic memory is a belief. Every newly stored belief references at least
one retrievable Evidence record. All referenced evidence and lineage memories
must belong to the belief's repository. Missing or cross-repository references
refuse the complete write before a belief row, vector, or relationship is
created. Evidence may exist without a belief; a belief may not exist without
evidence.

Episodic and raw writes are source observations. Their exact input is first
stored as Evidence and then linked to the compatibility memory record in the
same transaction. Semantic promotion uses those Evidence IDs; it never copies a
generated belief back into the evidence store as a fabricated citation.

Legacy callers that do not supply repository identity write into the reserved
`__visp_unscoped__` compatibility bucket. Those records and their Evidence are
forced to unknown provenance regardless of caller claims. The reserved scope is
never a valid recall, injection, context, or graph-request scope; its rows remain
available only through direct administrative list/get inspection.

### Public surfaces

Storage backends expose `store_evidence`, `get_evidence`, `list_evidence`, and
`update_evidence`. The update operation always raises an immutability error.
`SemanticMemory.establish` requires explicit evidence IDs. Higher-level capture
adapters may create evidence from the exact caller/tool/artifact input before
promotion, but HTTP semantic-belief creation requires existing evidence IDs.

Remote and HTTP evidence payloads preserve the same fields and repository
checks. A backend that cannot represent Evidence separately must refuse the
operation as unsupported; it must not silently store Evidence as a generic
Memory.

ArcadeDB and the optional Neo4j backend implement the Evidence graph. Neo4j
commits governed writes, citations, and explicit lineage in one transaction.

### Schema version 3 migration

Evidence was introduced by schema version 3; the current SQLite schema is 5.
Use the [backup and upgrade procedure](STORAGE.md#backup-and-schema-upgrades) for
existing stores. The Evidence transform from a version-2 store is idempotent:

1. Create the Evidence store and belief-to-evidence references.
2. Mirror each legacy memory into deterministic `legacy_import` Evidence using
   the original content and a full SHA-256 hash.
3. Link each legacy semantic belief to its mirrored Evidence.
4. Preserve every memory ID, repository, lifecycle status, timestamp, lineage,
   and relationship.
5. Mark legacy beliefs `provenance:unknown` and `legacy_unreviewed` without
   inventing an authority, citation, or approval. They remain quarantined until
   an explicit later review.

Retrying the migration produces no duplicate Evidence or references. The
version marker advances only after the transform and integrity checks succeed.
A store whose version is newer than this build is refused; automatic downgrade
is forbidden. SQLite migration requires an explicit backup path and leaves the
source copy available as the rollback artifact. Version-2 ArcadeDB and Neo4j
stores fail closed as unsupported until a separately rehearsed backend-native
migration is available; they are never version-bumped without transformation.

### Export and import

Export format 2.0 contains Evidence, beliefs/memories, intents, and the complete
relationship graph. Stable IDs, content hashes, lifecycle state, lineage,
relationship IDs, and relationship evidence are data, not regeneration hints.
Import validates repository ownership and references, stores Evidence first,
then beliefs, then relationships, and refuses malformed or dangling graphs.
Import does not replay caller-claimed trust or promote legacy Evidence.
Exports request one sentinel row beyond the supported 10,000-record ceiling per
record kind and refuse rather than silently truncate. SQLite import precomputes
and reconciles vector entries before committing the relational graph; vector
failure rolls back the graph and compensates newly written vector IDs. Access
telemetry remains target-local operational state, so a repeated identical import
does not overwrite or conflict with access counts advanced after the first import.

Backend portability is capability-gated:

| Backend | Complete graph export | Atomic graph import |
| --- | --- | --- |
| SQLite `LocalStorage` | Yes | Yes |
| ArcadeDB | Yes | No |
| Remote/HTTP | No | No |
| Neo4j | No | No |

Unsupported export is refused before storage reads or destination-file writes.
Unsupported import is refused before backend writes. ArcadeDB can provide a
complete, ceiling-checked graph export, but it does not currently expose the
single-transaction graph import needed by format 2.0. Remote/HTTP list endpoints
are paginated and do not expose every portable field, so the client does not
claim a complete export. Neo4j uses a separate native graph backup/restore command;
portable memory packs remain unavailable.
