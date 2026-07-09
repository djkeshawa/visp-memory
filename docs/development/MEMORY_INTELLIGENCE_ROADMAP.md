# Memory Intelligence Roadmap

This roadmap turns the Graphify comparison into a practical path for improving
LLM Memory without copying Graphify's product shape. Graphify is strongest as a
project/corpus graph generator. LLM Memory should become stronger as a durable,
temporal, team-aware memory system with explainable graph recall.

## Strategic Thesis

LLM Memory should not compete by adding every source parser, media extractor, or
static graph artifact that Graphify has. It should compete by answering a harder
agent question:

> What should this assistant remember from past work, why should it trust that
> memory, how is it connected to the current task, and did using it improve the
> outcome?

That requires four capabilities working together:

- Durable episodic, semantic, and intent memory.
- Evidence-backed relationships between memories.
- Graph-shaped recall that explains relevance paths.
- Feedback loops that improve future recall from observed usefulness.

## Source Observations

Graphify observations are based on its `v8` README, architecture notes,
how-it-works docs, and inspected code. Relevant documented facts:

- Graphify's pipeline is staged as detect, extract, build graph, cluster,
  analyze, report, and export.
- Its extractors return nodes and edges with stable IDs, source files, source
  locations, relationship types, and confidence labels.
- Its reports emphasize god nodes, surprising connections, ambiguous edges,
  communities, suggested questions, and token savings.
- It uses SHA256 file fingerprints and cache directories to skip unchanged work.
- It installs assistant guidance and hooks that nudge agents to query the graph
  before broad file reads.

LLM Memory current-state observations:

- LLM Memory already has the more relevant domain model for durable agent memory:
  episodic memories, semantic knowledge, and intent.
- It already has local/server storage, REST API, MCP tools, dashboard, auth,
  repository scoping, team models, relationships, compression, deduplication,
  and proactive recall.
- The current graph is mostly a visualization/query-adjacent surface, not yet a
  first-class retrieval, quality, and governance surface.

## Comparison And Impact Ranking

Impact is ranked by expected effect on agent quality, trust, and repeatability.

| Rank | Capability | Graphify strength | LLM Memory current state | LLM Memory opportunity |
|---:|---|---|---|---|
| 1 | Durable temporal memory | Static or periodically rebuilt corpus graph | Strong episodic, semantic, and intent model | Use time, outcomes, and intent as primary differentiators |
| 2 | Explainable graph recall | Strong graph query, path, and explain flows | Vector/lexical recall plus basic graph endpoint | Add trace recall over memory relationships |
| 3 | Relationship trust | Explicit confidence labels and source evidence | Relationship type and strength only | Add confidence, evidence, source, and reason fields |
| 4 | Memory health reporting | Strong graph report | Context summary only | Add memory intelligence report |
| 5 | Incremental maintenance | File hashes, update mode, hooks | Capture exists but no unified freshness manifest | Add capture manifest and idempotent refresh |
| 6 | Assistant guidance | Broad platform installers and always-on nudges | Good MCP workflow tools | Strengthen project-scoped recall-before-work guidance |
| 7 | Team/server memory | Mostly local artifact model | Strong API, auth, repos, teams | Make team memory governance a core advantage |
| 8 | Usefulness learning | Query logs, but not durable memory utility | No explicit recall utility loop | Track surfaced, used, ignored, and resolved memories |
| 9 | Privacy/security posture | Clear local tool threat model | Auth docs exist, memory threat model less explicit | Add memory-specific privacy and retention guidance |
| 10 | Broad corpus parsing | Very strong parser/media surface | Not a goal | Avoid for now; integrate later only when it serves memory |

## Design Principles

- Preserve LLM Memory's identity: memory over static corpus mapping.
- Prefer evidence-backed memory relationships over opaque similarity.
- Keep improvements backend-portable across SQLite, Neo4j, and RemoteStorage.
- Make every new retrieval behavior measurable with recall quality metrics.
- Keep local use simple; do not make Neo4j or heavy parser dependencies
  mandatory for individual developers.
- Add implementation through scoped tasks, not broad rewrites.

## Roadmap Tracks

### Track 1: Evidence Graph Foundation

Why:
Agents need to know whether a relationship is directly observed, inferred from
similarity, manually asserted, or uncertain. Without that, memory recall can
sound authoritative when it is only weakly related.

Current gap:
Relationships have `relationship` and `strength`, but not a durable explanation
of why they exist.

Target:
Every memory relationship can carry:

- `confidence`: `observed`, `inferred`, `ambiguous`, or `manual`
- `confidence_score`: bounded 0.0 to 1.0
- `source`: capture channel or API origin
- `source_file` and `source_location` where applicable
- `reason`: short explanation suitable for agent context
- `created_by` and `created_at`

Acceptance criteria:

- SQLite, Neo4j, API, and RemoteStorage expose the same relationship metadata.
- Existing relationships continue to load with safe defaults.
- Dashboard graph can show confidence and source for an edge.
- MCP can include relationship evidence in recall output.

Validation:

- Migration tests prove existing databases still work.
- API contract tests verify metadata round trips.
- Recall tests verify weak or ambiguous relationships are labeled, not hidden.

Impact:
Very high. This creates the trust substrate for all later improvements.

### Track 2: Graph-Shaped Recall

Why:
Vector search finds similar text. Graph recall explains connected context:
"this warning matters because it is linked to the bug fixed last week and the
active refactor goal."

Current gap:
`memory_recall` returns a ranked list. The graph endpoint returns nodes and
links for visualization, not an agent-optimized subgraph.

Target:
Add graph recall tools and API paths:

- `memory_neighbors(memory_id, relationship_filter=None)`
- `memory_path(source, target, max_hops=4)`
- `memory_trace(query, depth=2, token_budget=2000)`
- `memory_why_relevant(query, memory_id)`

Recommended algorithm:

1. Use existing ranking to find seed memories.
2. Expand through relationships with depth and token limits.
3. Penalize or stop at high-degree generic hubs.
4. Sort output by relevance, importance, confidence, recency, and intent match.
5. Return compact nodes plus evidence-backed edges.

Acceptance criteria:

- MCP returns trace output that includes memories and relationship reasons.
- API returns structured nodes/edges for dashboard and clients.
- Token budget truncation is deterministic and explains what was omitted.
- Works with SQLite and Neo4j.

Validation:

- Golden tests for trace ordering and truncation.
- Small fixture graph with expected shortest paths.
- Compare precision of list recall versus trace recall on repeated scenarios.

Impact:
Very high. This makes LLM Memory more explainable than ordinary vector memory.

### Track 3: Memory Intelligence Report

Why:
Graphify's report is valuable because it tells the user what the graph means.
LLM Memory needs an equivalent for durable memory health.

Target:
Add `llm-memory report` and a dashboard view with:

- High-impact memories by use, importance, and relationship degree.
- Fragile areas with repeated warnings or bug memories.
- Stale active intents.
- Ambiguous or low-confidence relationships.
- Isolated warnings that never surface.
- Contradiction candidates.
- Cross-repo risk paths through dependency relationships.
- Suggested questions that the memory graph is well positioned to answer.

Acceptance criteria:

- Report is deterministic on a fixed database.
- Report has text and JSON modes.
- Dashboard can consume the JSON mode.
- Report distinguishes facts from inferred recommendations.

Validation:

- Fixture database produces stable report sections.
- Report generation handles empty and small databases gracefully.
- Report has clear thresholds documented in code and docs.

Impact:
High. It turns stored memory from a passive database into an inspectable system.

### Track 4: Incremental Freshness And Capture Deduplication

Why:
A memory system becomes noisy when it records the same commit, test failure, or
conversation insight repeatedly. Freshness tracking keeps recall precise and
storage growth controlled.

Target:
Add a capture manifest that records:

- capture source type: git, test, conversation, manual, import, API
- source identity: commit SHA, file path, conversation ID, test run ID
- content hash
- last captured time
- output memory IDs
- capture version

Acceptance criteria:

- Re-running capture on unchanged inputs creates no duplicate memories.
- Changed inputs update or supersede prior capture records deterministically.
- Manifest survives export/import.
- Capture can report "changed", "unchanged", and "stale" counts.

Validation:

- Repeated capture integration tests assert stable memory counts.
- Changed content tests assert expected updates.
- Import/export tests preserve manifest data.

Impact:
High. It improves signal quality and makes future automation safer.

### Track 5: Usefulness Feedback Loop

Why:
Scientific retrieval improvement requires observed outcomes. A memory that is
frequently surfaced but ignored should lose priority. A warning that prevents a
bug should gain priority.

Target:
Track recall events and outcomes:

- memory surfaced
- memory selected or copied into context
- memory dismissed
- memory linked to a completed task
- memory led to new warning, decision, or bug fix

Potential signals:

- `shown_count`
- `used_count`
- `dismissed_count`
- `last_used_at`
- `utility_score`
- `outcome_notes`

Acceptance criteria:

- Recall event logging is optional and privacy-conscious.
- Utility score affects ranking only within bounded limits.
- Users can inspect and reset utility signals.
- No full prompt/response logging by default.

Validation:

- Ranking tests prove utility cannot dominate direct relevance.
- Dashboard shows useful versus noisy memories.
- Evaluation fixtures demonstrate improved top-k precision after feedback.

Impact:
High. This is a major differentiator over static graph systems.

### Track 6: Intent-Aware Retrieval

Why:
Relevance depends on current work. A memory about packaging is critical during a
release task and noise during dashboard styling.

Target:
Use active intent as a ranking and filtering signal:

- current task
- active goals
- constraints
- files under change
- repo scope and dependency scope

Acceptance criteria:

- `memory_session_start` records current task and uses it for recall.
- `memory_before_change` combines file, task, and intent-aware trace recall.
- Ranking output exposes the factors that contributed to relevance.

Validation:

- Same query with different active intents produces different but explainable
  ordering.
- Constraints are never silently omitted when relevant to a file or task.

Impact:
High. This uses LLM Memory's intent layer in a way Graphify cannot naturally
match.

### Track 7: Assistant Integration And Always-On Guidance

Why:
Memory is only useful if assistants consistently ask for it at the right time.
Graphify's always-on guidance is effective because it changes agent behavior.

Target:
Improve project-scoped assistant guidance:

- Codex/Claude/Cursor/Aider instructions that prefer `memory_session_start`
  before work.
- Hook or instruction patterns that nudge agents to use `memory_before_change`
  before edits.
- Clear uninstall/repair/version checks for installed guidance.

Acceptance criteria:

- Project-scoped install writes minimal, reviewable files.
- Instructions do not block ordinary tool use.
- Installed guidance points to current command names.
- Docs explain when to use broad context versus trace recall.

Validation:

- Installer tests for generated instruction files.
- MCP smoke tests for session start and before-change flows.

Impact:
Medium-high. It raises adoption and consistency without changing storage.

### Track 8: Memory Privacy, Security, And Governance

Why:
LLM Memory stores decisions, incidents, warnings, conversation summaries, and
team context. That is more sensitive than a static project graph.

Target:
Add a documented trust model:

- what data is stored
- what data may contain secrets
- which providers receive text
- local versus server mode boundaries
- retention and deletion behavior
- auth defaults
- team access expectations
- safe capture guidance

Acceptance criteria:

- Documentation includes a memory-specific threat model.
- Server APIs document auth and tenancy assumptions.
- Capture docs warn about secrets and high-sensitivity conversations.
- Tests cover obvious cross-repo/team authorization boundaries.

Validation:

- Security review checklist exists before production-candidate release.
- Auth and scoped-access tests remain mandatory release gates.

Impact:
Medium-high. It is required for team trust and production readiness.

### Track 9: Dashboard Memory Intelligence

Why:
The dashboard should not only show that memories exist. It should help users
inspect memory quality and decide what to fix.

Target:
Dashboard views for:

- evidence graph with edge confidence
- memory health report
- stale intents
- duplicate/conflict candidates
- high-utility and low-utility memories
- capture freshness

Acceptance criteria:

- Dashboard uses existing API contracts, not private storage access.
- Empty states explain what action creates useful data.
- Browser smoke tests cover report and graph views.

Validation:

- Playwright smoke for dashboard pages.
- API fixture tests for report JSON.

Impact:
Medium. Useful after backend semantics are stable.

### Track 10: Evaluation Harness

Why:
Claims like "better recall" need repeatable measurement. Without an evaluation
set, ranking changes can look plausible while regressing real workflows.

Target:
Create small benchmark fixtures:

- repeated bug workflow
- refactor with warnings
- release task with constraints
- cross-repo dependency task
- stale intent cleanup

Metrics:

- top-k recall precision
- evidence-path coverage
- duplicate rate after repeated capture
- token budget used per useful memory
- stale memory surfacing rate
- time to generate report

Acceptance criteria:

- Evaluation runs locally without network calls.
- Baseline metrics are stored in docs or test snapshots.
- Ranking changes update expected metrics intentionally.

Impact:
Medium. It improves scientific confidence and protects future changes.

## Phased Delivery Plan

### Phase 0: Alignment And Specification

Goal:
Define the contracts before changing storage.

Deliverables:

- Relationship metadata schema proposal.
- Relationship evidence contract in
  [MEMORY_INTELLIGENCE_CONTRACTS.md](MEMORY_INTELLIGENCE_CONTRACTS.md).
- Trace recall API/MCP acceptance criteria.
- Report JSON schema draft.
- Capture manifest schema draft.
- Evaluation fixture outline.

Exit criteria:

- Each schema has examples.
- Backward compatibility is explicitly documented.
- Implementation tasks are small enough for focused task execution.

### Phase 1: Evidence Graph Core

Goal:
Make memory relationships trustworthy and portable.

Deliverables:

- Relationship metadata in SQLite and Neo4j.
- API and RemoteStorage round trips.
- Dashboard graph edge metadata support.
- Migration tests.

Exit criteria:

- Existing relationship behavior still works.
- New relationships can explain why they exist.
- Confidence defaults are safe for old data.

### Phase 2: Trace Recall And Memory Report

Goal:
Turn the graph into an agent retrieval and diagnostic surface.

Deliverables:

- `memory_trace`, `memory_neighbors`, and `memory_path`.
- `/graph/trace` or equivalent API endpoint.
- `llm-memory report` text and JSON.
- MCP formatting with evidence paths.

Exit criteria:

- Agents can ask why a memory is relevant.
- Users can inspect memory health without opening the dashboard.
- Tests prove deterministic output under token budgets.

### Phase 3: Freshness And Feedback

Goal:
Reduce memory noise and use observed outcomes to improve ranking.

Deliverables:

- Capture manifest.
- Idempotent git/test/conversation capture.
- Recall event model.
- Utility score and bounded ranking integration.

Exit criteria:

- Repeated capture does not duplicate memories.
- Useful memories become easier to retrieve.
- Ignored memories do not dominate future context.

### Phase 4: Intent And Team Intelligence

Goal:
Use LLM Memory's unique model to exceed static graph systems.

Deliverables:

- Intent-aware trace recall.
- Cross-repo memory path queries.
- Team-scoped report sections.
- Governance docs and dashboard surfaces.

Exit criteria:

- Recall changes with active task and explains why.
- Cross-repo dependencies surface relevant warnings and decisions.
- Team server users can inspect memory quality and ownership.

## Deferred Or Rejected For Now

These are intentionally not first-wave goals:

- Broad tree-sitter extraction for many languages.
- PDF, image, audio, and video ingestion.
- Committed generated graph artifacts as the primary team workflow.
- Mandatory Neo4j for local users.
- New background services or queues.

Reason:
They add operational and dependency complexity before the existing memory system
has evidence-backed retrieval, freshness, and measurable usefulness.

## Scientific Validation Plan

Each major improvement should be treated as a falsifiable hypothesis.

| Hypothesis | Measurement | Success threshold |
|---|---|---|
| Evidence-backed relationships improve trust | Percent of recalled memories with an evidence path | At least 90 percent for graph recall results |
| Trace recall improves task relevance | Top-5 useful recall precision on fixtures | Better than current list recall baseline |
| Freshness manifest reduces noise | Duplicate memories after repeated capture | Zero duplicates for unchanged inputs |
| Utility feedback improves retrieval | Useful memory rank after positive feedback | Rank improves without bypassing direct query relevance |
| Intent-aware recall improves context | Constraint/warning surfacing in task fixtures | Relevant constraints present in first response |
| Report improves maintainability | Stale intents and ambiguous links found in fixtures | 100 percent of seeded issues reported |

## Risks And Mitigations

| Risk | Mitigation |
|---|---|
| Relationship schema churn breaks old databases | Add migrations, defaults, and compatibility tests first |
| Graph recall returns too much context | Require depth and token budgets with deterministic truncation |
| Utility feedback creates popularity bias | Bound utility contribution and expose ranking factors |
| Capture manifest loses data lineage | Preserve source IDs and manifest through export/import |
| Dashboard work outruns backend contracts | Implement report and trace JSON contracts before UI |
| Team privacy expectations are unclear | Publish memory-specific threat model before production candidate |

## Task Breakdown Template

Each implementation task should include:

- Requirement and acceptance criteria.
- Storage/API/MCP/dashboard surfaces touched.
- Backward compatibility expectations.
- Test plan.
- Migration or rollback notes.
- Documentation update.

Recommended initial tasks:

1. Specify relationship metadata schema and migration plan.
2. Implement relationship metadata in LocalStorage.
3. Implement relationship metadata in Neo4jStorage.
4. Add relationship metadata API and RemoteStorage support.
5. Add graph trace query service over existing storage.
6. Expose trace recall through MCP.
7. Add memory report generator.
8. Add report CLI command.
9. Add capture manifest for git capture.
10. Add evaluation fixtures for recall and capture quality.

## Success Definition

LLM Memory is better than Graphify for agent memory when:

- It can explain not only what is connected, but why the connection matters now.
- It remembers temporal work history, decisions, active constraints, and team
  context across sessions.
- It improves recall using observed usefulness rather than only static topology.
- It can prove freshness, provenance, and confidence for the context it injects.
- It remains simple enough for local developers while scaling to team servers.
