# LLM Memory — 2026 Improvement Synthesis

This document captures a research-grounded improvement pass focused on the project's
core intent: **let coding assistants (Claude Code, Codex, Copilot, Cursor, Aider) work
with fewer tokens and without re-reading files, by giving them an active, human-brain-
inspired memory.** It records the competitive landscape, where this project already
wins, the highest-leverage gaps, and the improvements implemented in this pass.

## 1. Competitive landscape (2025–2026)

| System | Strongest at | Memory model | Coding-agent fit |
|---|---|---|---|
| **Graphify** | Structural code graph; token savings on *structural* queries; integration breadth (MCP + PreToolUse/git hooks across 20+ agents); edge provenance | Static NetworkX graph rebuilt from files (tree-sitter + LLM); no episodic/semantic/intent layers | High, but graph-of-code, not durable memory |
| **Zep / Graphiti** | Temporal correctness (bi-temporal fact validity, edge invalidation, point-in-time queries) | Temporal knowledge graph | Conversation memory, not code-aware |
| **mem0 / OpenMemory** | Drop-in memory layer; widest storage choice; multi-tool MCP sharing | LLM fact-extraction + ADD/UPDATE/DELETE | Good, but building blocks |
| **Letta (MemGPT)** | Memory native to the agent; self-editing tiers; **sleeptime consolidation** | Core/recall/archival tiers | Wants you to use *its* agent |
| **Cognee** | Unified relational+vector+graph; ontology grounding; a real `codify` code-graph pipeline (Python-only) | ECL pipeline | Good, LLM-heavy |
| **Supermemory** | Best small-lib coding UX (plugins, AST chunking, ~50ms inject, auto-forget) | Profiles + facts | High |
| **Incumbents** (CLAUDE.md, Cursor rules, Codex/Copilot memory) | Zero-setup, automatic | Static files concatenated in full; or new, narrow, "don't rely on it" auto-memory | Automatic but **no relevance-ranked retrieval, no decay, re-injected every session** |

**The white space nobody owns:** *automatic, explainable, decaying, team-shared coding
memory that replaces file reads.* The defensible wedge is the intersection of
(a) automatic capture + injection via hooks (not tool-call-dependent),
(b) brain-inspired layered memory with real decay/consolidation,
(c) per-fact explainable provenance, and
(d) cross-repo / team scope.

## 2. What this project already does exceptionally well

This project **already owns (b), (c), and (d)** — a rare position:

- **Three-layer cognitive model** (episodic / semantic / intent) — the right shape for
  durable agent memory; competitors mostly store flat facts or a code graph.
- **Evidence-backed graph recall** (`memory_trace`, `memory_neighbors`, `memory_path`,
  `memory_why_relevant`) with confidence/source/reason on every edge — the explainability
  most competitors lack.
- **Hierarchical consolidation** (episodic → semantic → principle), **exponential decay**,
  **usefulness feedback**, **intent-aware ranking with explainable factors**.
- **Backend portability** (SQLite / ArcadeDB / Neo4j / Remote), **teams + cross-repo**,
  deterministic and thoroughly tested.

The strategy is therefore **not** to chase Graphify's code-graph breadth, but to deepen
the brain-inspired memory and make its token advantage *measurable and automatic*.

## 3. Improvements implemented in this pass

All are backend-portable, backward compatible, and fully tested.

### 3.1 Token-efficiency ledger (make the core promise measurable)

The whole value proposition is replacing file reads with compact memory — but nothing
measured it. Added:

- `core/tokens.py` — deterministic token estimator (`tiktoken` when available, calibrated
  heuristic otherwise) and a conservative `TokenSavings` type (never negative).
- Compression now records **auditable** `token_savings` on the resulting semantic memory
  (many episodic memories → one compact memory is a real, source-grounded saving).
- `Memory.token_efficiency()` aggregates consolidation savings + context compactness.
- `llm-memory tokens` CLI (text / JSON).

### 3.2 MCP `core` tool profile (reduce per-session token overhead)

Every advertised MCP tool definition costs context tokens in *every* session. The full
surface is ~3,000 tokens of schema before any work begins.

- `LLM_MEMORY_MCP_PROFILE=core|full` (default `full` for compatibility). `core` advertises
  the everyday recall-before-work / record-after-work loop including the feedback tool the
  reinforcement loop needs (16/34 tools), saving **~1,250 tokens per session (~41% of
  tool-schema overhead)**. Hidden tools still work if
  called by name.

### 3.3 Retrieval-induced reinforcement ("use it or lose it")

The brain strengthens what it retrieves. `access_count` was tracked but never used, and
plain recall never reinforced.

- `ranking.activation_rank_adjustment` — a bounded **frequency-based** activation factor from
  `access_count` (a simplified base-level activation in the spirit of ACT-R / Memary; recency is
  handled separately by the recency term in `score_memory_result`), capped so it tunes ties
  without ever overriding direct query relevance. The *combined* boost from all secondary signals
  (utility + context + activation) is itself capped (`TOTAL_ADJUSTMENT_LIMIT`) so no stack of weak
  signals can override a strong direct match.
- `storage.log_recall_event` now reinforces a memory (access_count++, accessed_at refresh)
  on positive **use** events (`used` / `task_linked` / `outcome_linked`) — resetting decay
  and boosting future recall. Mere surfacing/dismissal does not reinforce (no popularity
  bias from exposure).

This closes a loop across three previously-disconnected subsystems: feedback → activation
→ decay.

### 3.4 Frequency-aware decay (spaced repetition)

Completes the brain mechanism above. MemoryBank ([arXiv 2305.10250](https://arxiv.org/abs/2305.10250))
models retention as `R = e^(−t/S)` where strength `S` grows on each recall, flattening the
forgetting curve. We model `S` as a function of `access_count` and stretch the decay
half-life accordingly (`ranking.effective_halflife_days` / `ranking.projected_importance`,
shared by the decay job and the MCP decay preview). A memory used many times fades far more
slowly; a never-used memory decays exactly as before (backward compatible). Together, 3.3 +
3.4 realize spaced-repetition *dynamics* (inspired by MemoryBank rather than reproducing its
exact curve): `t ← 0` on use (accessed_at refresh) and a growing `access_count` that lengthens
the half-life via a **saturating** function (`log1p`), so the first recalls help most and the
effect levels off — avoiding runaway persistence.

## 4. Research-grounded roadmap (papers, prioritized)

From a SOTA papers sweep (2024–2026). Build order follows impact-to-effort; Tier 1 items
compose and target the project's weakest area (multi-hop + temporal precision) while cutting
tokens. Mechanisms are trusted over contested cross-system leaderboard numbers.

### Tier 1 — highest leverage (compose well)

1. **Spreading activation at recall (Personalized PageRank).** HippoRAG / HippoRAG 2
   ([2405.14831](https://arxiv.org/abs/2405.14831), [2502.14802](https://arxiv.org/abs/2502.14802)).
   The project already has graph relationships and graph-trace recall; add PPR so a few seed
   nodes activate a whole associative neighborhood in one pass. Algorithm: extract query/intent
   entities → match to graph nodes (seeds); weight each seed by **node specificity**
   `s_i = 1/(#memories the entity appears in)`; run PPR (**damping 0.5**); score each memory by
   the **PPR mass over the nodes it contains**; blend with the existing intent score. Cheap
   variant (recommended first): 2-hop bounded BFS from seeds with exponential hop-decay weights
   that **accumulate across converging paths** — ~80% of the benefit, and a natural extension of
   the current single-path `_expand`. This is the literal "spreading activation" brain analogy.
2. **Bi-temporal validity + non-destructive edge invalidation.** Zep/Graphiti
   ([2501.13956](https://arxiv.org/abs/2501.13956)). Add to every semantic edge two time axes —
   **valid time** (`valid_at`, `invalid_at`) and **transaction time** (`created_at`, `expired_at`).
   On a contradicting write, set the old edge's `invalid_at = new.valid_at` (don't delete). The
   project already records `contradicts` edges with evidence — this makes them temporal and
   auditable ("this API was renamed in commit X"), fixing the hardest benchmark category.
3. **Write-time reconciliation: ADD / UPDATE / DELETE / NOOP.** Mem0
   ([2504.19413](https://arxiv.org/abs/2504.19413)). For each new fact, retrieve top-k similar
   existing memories and decide one operation, keeping the store small, deduplicated, and
   self-correcting — the mechanism behind Mem0's >90% token savings. Pairs with #2 (DELETE →
   soft-invalidate). Builds on the existing dedup + conflict-detection.

### Tier 2 — strong additions

4. **Offline "sleep-time" consolidation with prediction-error distillation.** Sleep-time compute
   ([2504.13171](https://arxiv.org/abs/2504.13171)), Nemori ([2508.03341](https://arxiv.org/abs/2508.03341)).
   Make compression a scheduled offline job (never on the interactive path) and store **only the
   surprising delta** vs. what memory already predicts — ideal for coding sessions where most of
   a session confirms known facts. Host for #3 and #6 below.
5. **Salience-gated importance + reflection trigger.** Generative Agents
   ([2304.03442](https://arxiv.org/abs/2304.03442)). LLM-rate poignancy (1–10) at write time so
   high-signal decisions start far above routine events; fire consolidation when accumulated
   importance crosses a threshold (event-driven, not just periodic). (recency decay constant is
   **0.995**, a common mis-cite is 0.99.)
6. **Reflection / insight generation with evidence pointers + governance.** Generative Agents +
   ExpeL ([2308.10144](https://arxiv.org/abs/2308.10144)). Synthesize higher-level insights, store
   them with citations to evidence memories (extends the existing evidence-backed edges), and track
   reliability via UPVOTE/DOWNVOTE from the usefulness loop — uncurated experience can degrade below
   baseline, so governance matters.

### Tier 3 — token-efficiency polish

7. **Query-aware context pruning before injection.** Provence
   ([2501.16214](https://arxiv.org/abs/2501.16214)) / RECOMP ([2310.04408](https://arxiv.org/abs/2310.04408)).
   Prune retrieved memories to relevant sentences in one cross-encoder pass (49–84% near-lossless),
   or emit empty when a memory is irrelevant (selective augmentation) — the last-mile token win.
8. **Working-memory tier with memory-pressure flushing.** MemGPT
   ([2310.08560](https://arxiv.org/abs/2310.08560)). A small capacity-limited active set (current
   task, active files, recent decisions) that summarizes-and-evicts oldest items into episodic store
   at ~70% budget — mirrors human working memory, keeps the interactive context tight.

### Cross-cutting / hygiene

- **Hook-based automatic injection** — the biggest remaining gap vs. incumbents: a PreToolUse-style
  hook that intercepts file-read/grep and returns relevant remembered facts first, plus SessionStart
  injection, so memory is used automatically rather than only on tool calls.
- **Ingest the static-file zoo as seed memory** — import existing `CLAUDE.md`, `AGENTS.md`,
  `.cursor/rules`, `copilot-instructions.md`, `memory-bank/` into the relevance-ranked store.
- **Standardize timestamps on UTC** — fix a latent local-vs-UTC inconsistency that skews
  decay/recency on non-UTC machines (flagged as a separate task).

### Brain-analogy coverage

Implemented this pass: **forgetting curve / spaced repetition** (§3.3 + §3.4),
**base-level activation** (§3.3), **measured consolidation savings** (§3.1). The roadmap then adds
**spreading activation** (#1), **reconsolidation / belief revision** (#2), **write-time
consolidation** (#3), **systems consolidation / sleep** (#4), **salience** (#5), **reflection**
(#6), and **working memory** (#8) — a faithful hippocampal–neocortical model.

## 5. Validation

- Deterministic unit tests for every new mechanism (token estimation/savings, base-level
  activation, reinforcement-on-use, spaced-repetition decay) plus CLI/MCP contract tests and a
  decay integration test proving used memories are spared.
- Full suite green (only pre-existing Windows tempdir-cleanup teardown errors remain, which
  pass in isolation).
- All new ranking adjustments are bounded and covered by "cannot override direct relevance"
  tests, preserving the existing ranking invariants.
