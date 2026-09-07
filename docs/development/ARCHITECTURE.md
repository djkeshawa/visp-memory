# Architecture and data flow

Visp Memory is a storage and retrieval service for project knowledge. A coding
assistant calls it for context, then decides and executes the work itself.
The default deployment uses SQLite; the dashboard is a static Next.js application
served by the same FastAPI process as the API.

## Components

```mermaid
flowchart TB
    Assistant["Coding assistant"] --> MCP["MCP tools and hooks"]
    Developer["Developer"] --> CLI["Command-line interface"]
    Developer --> UI["Dashboard"]
    UI --> API["FastAPI
Authentication and project access"]
    MCP --> Core["Memory services
Capture, recall, briefs, and maintenance"]
    CLI --> Core
    API --> Core
    Core --> SQL[("SQLite
Memories, evidence, intents, and journals")]
    Core --> Neo4j[("Optional Neo4j backend
Memories, Evidence, workflow and dreaming journals")]
    Core -.-> Vector[("Optional Chroma index")]
    Core -.-> Embed["Optional embedding provider"]
```

Local CLI and stdio MCP use project configuration directly. Remote clients call
an authenticated server, which owns storage and provider configuration. The
SQLite is the default; Neo4j is an opt-in beta with native vectors.
[Backend limits and backup requirements](STORAGE.md#choose-a-backend) are explicit.

## What is stored

| Record | Purpose | Example |
|---|---|---|
| Evidence | Immutable source content with provenance and a content hash | A captured test result or original observation |
| Episodic memory | Something that happened in the project | A migration failed because an index was missing |
| Semantic memory | Reusable knowledge citing evidence | The migration requires that index |
| Intent | A goal, constraint, or direction, with reported outcome history | Add retry handling without changing the public API |
| Relationship | A link between records or code references | A later note supersedes an older decision |

Evidence and conclusions remain separate. Corrections create new source records;
missing or cross-project citations cause a semantic write to be refused.
See [storage contracts](CONTRACT_SURFACE.md#evidence-and-belief-storage-contract).

## Capture and write

```mermaid
flowchart TD
    Input["CLI, MCP, API, or capture adapter"] --> Validate["Validate project, source channel, and content"]
    Validate --> Kind{"Write type?"}
    Kind -->|"Observation"| Evidence["Create immutable evidence"]
    Evidence --> Event["Save episodic memory linked to evidence"]
    Kind -->|"Belief"| Cite["Check existing same-project evidence"]
    Cite --> Belief["Save semantic memory with citations"]
    Event --> Store[("Persistent store")]
    Belief --> Store
    Store -.-> Index["Optional embedding and vector indexing"]
```

Adapters assign provenance from the write channel; a payload cannot grant itself
a trusted tier. Known secret patterns are redacted on normal capture. Imports
that would need rewriting are refused to preserve their content hashes.
[Trust and privacy](../TRUST.md) explains the boundary and its limits.

## Search and task context

Explicit search and automatic prompt injection have different purposes. Search
lets a user inspect candidates, including quarantined records within their scope.
Prompt context applies additional trust and budget checks.

```mermaid
flowchart TD
    Query["Task or query with project scope"] --> Candidates["Retrieve candidates
Keyword search or optional vectors"]
    Candidates --> Eligible["Filter project, access, time, and runtime scope"]
    Eligible --> Rank["Rank relevant candidates"]
    Rank --> Search["Explicit recall
Matches, sources, and explanations"]
    Rank --> Trust["Prompt context
Apply provenance and trust checks"]
    Structure["Optional file, symbol,
and relationship candidates"] -.-> Eligible
    Trust --> Brief["Cited task brief
Combine bounded retrieval channels"]
    Trust --> Inject["Automatic injection
Apply relevance, redundancy, and budget rules"]
    Brief --> Host["Assistant receives context or explicit unknowns"]
    Inject --> Host
```

This is a conceptual view of several retrieval surfaces, not a claim that every
endpoint runs an identical pipeline. Task briefs combine search, file/symbol,
and relationship signals; the machine recall contract can add structurally
related memories without displacing direct matches. Scores rank results; they
are not probabilities of correctness. The retrieval policy never calls an LLM.

### Injection policy

Automatic injection can return nothing when there is no useful signal. Defaults
from [the implementation](../../src/visp_memory/core/injection.py) are:

| Control | Default |
|---|---|
| Task specificity | At least 3 meaningful terms, unless a file is supplied |
| Small store | Below 5 memories, consider warnings only |
| Relevance floor | 0.55 |
| Distinction from weak matches | Margin 0.04, unless relevance reaches 0.68 |
| Output budget | At most 4 memories and 1,200 characters |
| Near-duplicate filter | Term overlap threshold 0.7 |

Hooks can use smaller budgets. Preview the actual selection and available
rejection counts with `visp-memory preview "fix session expiry" --file src/auth.py`.
Threshold changes should be evaluated against [selection benchmarks](../BENCHMARK.md).

## Maintenance and progress

[Dreaming](DREAMING.md) runs bounded, project-scoped cleanup in the server. It
merges eligible exact duplicates reversibly and leaves broader consolidation
and expiry proposals for review. It does not call a model or modify intent status.

[Workflow reports](WORKFLOW_REPORTS.md) mirror explicit status from the assistant
or external workflow that owns a task. Ordered reports, reporter identity, and
history prevent stale updates from replacing newer ones. Memory checks report
consistency; it does not verify the underlying work.

## Code map

Paths are relative to `src/visp_memory/`.

| Location | Responsibility |
|---|---|
| `interfaces/`, `hooks/`, `capture/` | CLI, MCP, host integration, and capture adapters |
| `server/` | HTTP access controls, API routes, and dashboard hosting |
| `core/memory.py`, `layers/` | Memory facade and record-specific operations |
| `core/storage.py`, `core/remote_storage.py` | SQLite storage and remote delegation |
| `core/eligibility.py`, `core/ranking.py`, `core/trust.py` | Eligibility, ranking, and prompt trust |
| `core/injection.py`, `core/task_brief.py` | Budgeted injection and cited context |
| `core/embeddings.py` | Provider selection and embedding generation |
| `core/dreaming/`, `core/intent_workflow.py` | Cleanup journals and external progress reports |

For exact API guarantees, use the [contracts reference](CONTRACT_SURFACE.md).
For changes, follow [Contributing](../../CONTRIBUTING.md) and [testing](TESTING.md).
