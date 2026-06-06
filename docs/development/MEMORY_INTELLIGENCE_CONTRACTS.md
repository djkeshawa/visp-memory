# Memory Intelligence Contracts

This document defines the portable contracts that should be implemented before
storage, API, MCP, or dashboard behavior changes in the memory intelligence
roadmap.

## Relationship Evidence Contract

Every memory relationship should be able to explain why it exists, where the
evidence came from, and how much confidence callers should place in it. Public
surfaces should preserve the existing relationship fields and add evidence as
optional additive metadata.

Preferred public shape:

```json
{
  "id": "rel_123",
  "source_id": "memory_a",
  "target_id": "memory_b",
  "relationship": "derived_from",
  "strength": 0.82,
  "evidence": {
    "confidence": "observed",
    "confidence_score": 0.9,
    "source": "git",
    "source_file": "src/example.py",
    "source_location": "L40-L58",
    "reason": "Both memories were captured from the same bug fix commit.",
    "created_by": "capture.git",
    "created_at": "2026-06-06T22:45:00Z"
  }
}
```

Dashboard graph links may keep their current graph field names, but should carry
the same normalized evidence object:

```json
{
  "source": "memory_a",
  "target": "memory_b",
  "label": "derived_from",
  "strength": 0.82,
  "evidence": {
    "confidence": "observed",
    "confidence_score": 0.9,
    "source": "git",
    "source_file": "src/example.py",
    "source_location": "L40-L58",
    "reason": "Both memories were captured from the same bug fix commit.",
    "created_by": "capture.git",
    "created_at": "2026-06-06T22:45:00Z"
  }
}
```

## Field Definitions

| Field | Type | Required after normalization | Safe default | Notes |
|-------|------|------------------------------|--------------|-------|
| `confidence` | enum | yes | `ambiguous` | One of `observed`, `inferred`, `ambiguous`, or `manual`. Use `ambiguous` when legacy data lacks provenance. |
| `confidence_score` | number | yes | bounded relationship `strength` when present, otherwise `0.5` | Must be between `0.0` and `1.0`. Scores are evidence strength, not retrieval relevance. |
| `source` | string or null | yes | `legacy` for migrated rows, otherwise `unspecified` | Capture channel or API origin such as `git`, `test`, `conversation`, `manual`, `api`, `mcp`, or `import`. |
| `source_file` | string or null | yes | `null` | Relative source path when the relationship can be tied to a file. Do not store absolute local paths by default. |
| `source_location` | string or null | yes | `null` | Human-readable location such as `L12-L20`, a commit range, test case name, or conversation segment. |
| `reason` | string | yes | `Legacy relationship without evidence metadata.` | Short explanation suitable for agent context. Keep it factual and concise. |
| `created_by` | string or null | yes | `null` | Actor or component that created the relationship, for example `capture.git`, `memory.linker`, `api`, or a user ID when appropriate. |
| `created_at` | ISO 8601 string or null | yes | existing relationship timestamp when available, otherwise `null` | Do not invent original creation time for legacy rows. |

## Confidence Semantics

- `observed`: Direct evidence links the memories, such as the same commit, test
  failure, source location, explicit capture event, or imported source record.
- `inferred`: The system derived the relationship from similarity, ranking,
  clustering, heuristics, or other indirect signals.
- `ambiguous`: The relationship exists, but the system lacks enough provenance to
  explain whether it was observed, inferred, or manual.
- `manual`: A user or trusted API caller explicitly asserted the relationship.

Rules:

- Weak or uncertain relationships should be labeled, not hidden.
- `confidence_score` must be clamped to `0.0 <= score <= 1.0`.
- `confidence` and `confidence_score` should not replace existing retrieval
  relevance scores. They describe relationship evidence only.
- Public output should include evidence when available and synthesize defaults
  when storage does not yet contain the fields.

## Compatibility Expectations

Existing data:

- Relationships without evidence metadata remain valid.
- Reads normalize missing evidence to the safe defaults above.
- Existing `relationship`, `strength`, `source_id`, `target_id`, and `created_at`
  behavior should continue to work.
- Migration or adapter code must not drop old relationships because evidence is
  missing.

Public API:

- Existing relationship create payloads remain valid when `evidence` is omitted.
- Relationship responses may add the `evidence` object without removing current
  fields.
- Graph responses should add evidence to links without changing current node and
  link identifiers.
- Validation should reject unsupported confidence values and out-of-bounds
  confidence scores.

RemoteStorage:

- RemoteStorage should pass through relationship evidence when the server
  supports it.
- Older servers that ignore evidence should remain usable.
- Client code should tolerate missing evidence in responses and normalize it for
  downstream callers.

MCP:

- MCP recall and graph-oriented output should include confidence, source, and
  reason for evidence-backed edges when the output has room.
- Compact output may omit null fields, but it should not omit an ambiguous
  confidence label when the relationship is shown.

Dashboard:

- Graph edge details should show confidence, source, and reason when present.
- Empty or legacy evidence should render as ambiguous or legacy provenance, not
  as high-confidence evidence.
- Dashboard code should consume public API evidence fields only.

## Storage Rollout Notes

SQLite:

- Additive columns or a JSON metadata column are acceptable if reads normalize to
  the public evidence object.
- Existing rows should be readable before and after migration.
- Tests should cover old rows with only `relationship` and `strength`.

Neo4j:

- Evidence should be stored as relationship properties and normalized to the same
  public evidence object.
- Relationship type normalization and unsafe type rejection must remain intact.
- Neo4j must remain optional for local users.

RemoteStorage:

- Treat evidence as an optional object on create and response payloads.
- Do not require server support before allowing existing relationship calls.

## Validation Requirements

The first implementation tasks that change behavior should add tests for:

- legacy relationship rows returning safe evidence defaults;
- new relationships round-tripping evidence metadata;
- unsupported confidence values being rejected at public input boundaries;
- confidence scores being bounded to `0.0` through `1.0`;
- API and RemoteStorage preserving omitted-evidence compatibility;
- MCP and dashboard surfaces labeling ambiguous relationships instead of
  presenting them as trusted facts.

## Out Of Scope For This Contract

- Graph trace algorithms and ranking formulas.
- Report section thresholds.
- Capture manifest persistence.
- Utility feedback scoring.
- Dashboard page design.
- New dependencies or mandatory Neo4j behavior.
