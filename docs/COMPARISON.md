# How this compares

Written to be useful when the answer is "use something else". Every competitor listed
here does something better than this project does, and those are stated first.

## Short version

| You want | Use |
|---|---|
| An assistant that remembers *you* across chats | mem0, Letta |
| Temporal fact tracking at enterprise scale, hosted | Zep / Graphiti |
| Zero setup, one machine, one tool | Claude Code's built-in auto memory |
| A codebase's history, curated, portable, auditable | this |

## Claude Code's built-in memory

**What it does better:** it is already installed, needs no configuration, and is
maintained by the people who make the assistant. `CLAUDE.md` survives compaction. Auto
memory writes notes without being asked, keeps a `MEMORY.md` index under a 200-line /
25 KB read budget, moves detail into topic files loaded on demand, and stamps a
`modified` timestamp so staleness is visible. `/memory` makes it all editable markdown.
For a single developer on one machine using one tool, that is a genuinely good answer and
this project is not obviously worth the extra install.

**Where the gaps are**, per [Anthropic's own documentation](https://code.claude.com/docs/en/memory):

- *Machine-local.* "Files are not shared across machines or cloud environments." Your
  laptop and your desktop have different memories, and your teammate has none of yours.
- *Per-tool.* Cursor, Codex, and Aider each keep their own; nothing is shared between
  them.
- *Accumulated, not curated.* What gets written is a per-session judgement call. There is
  no relevance ranking against the current task, no deduplication across entries, no
  supersession when a fact changes, no provenance, and no injection budget beyond the
  index size limit.

**This project is complementary, not a replacement.** It is portable across machines and
tools, and it ranks, budgets, deduplicates, and audits.

## mem0

**What it does better:** enormous adoption (~51k GitHub stars, 100k+ developers,
$24M raised, the memory provider for the AWS Agent SDK), a hosted platform, SDKs in
several languages, and published benchmark scores. If you need a memory layer for a
chat product today, it is the safe choice and this is not.

**Different problem.** mem0 is built to remember *a user* — preferences, facts,
conversation history. This is built to remember *a codebase* — decisions, reverts,
fragile files, conventions. Those need different retrieval, different capture, and
different failure handling.

**Caveat on the benchmarks:** mem0's headline numbers are on LoCoMo and LongMemEval,
which measure conversational recall. An independent audit found
[6.4% of LoCoMo's answer key is wrong and its judge accepts up to 63% of intentionally wrong answers](https://penfieldlabs.substack.com/p/we-audited-locomo-64-of-the-answer).
Neither benchmark measures anything about coding.

## Zep / Graphiti

**What it does better — and it is the closest competitor on the dimension this project
cares most about.** Zep's temporal knowledge graph gives every fact edge bi-temporal
validity (`valid_from`, `invalid_at`), automatic invalidation when a fact is superseded,
and full provenance for audit. That is a more complete temporal model than this project
has. It also reports a large lead over mem0 on LongMemEval.

**Where this differs:** Zep is a hosted service built for conversational and business
data. This runs locally with SQLite by default, is built around code, and defends against
*adversarial* memory — Zep's temporal model handles facts legitimately **changing**, not
records that were **hostile when written**. See [TRUST.md](TRUST.md).

**Caveat, applied evenly:** Zep's published 84% LoCoMo claim was
[corrected to ~58% after independent evaluation](https://github.com/getzep/zep-papers/issues/5).
That is a reason to distrust benchmark numbers in this field generally — including the
ones on this page, which is why [BENCHMARK.md](BENCHMARK.md) ships its reproduction
script and its negative results.

## agentmemory, Memorix, and other cross-tool memory tools

**What they do better:** they are simpler. If you want plain markdown memory shared
across Claude Code, Codex, and Cursor with minimal machinery, they get you there faster.

**Where this differs:** they inject on volume — agentmemory injects up to 16,000
characters of context per prompt. On the evidence in
[SWE-ContextBench](https://arxiv.org/abs/2602.08316), that is the wrong side of a
measurable trade: unfiltered context scored 12.12 points *below* curated context and cost
more than using no memory at all. On its automatic-injection path this project is capped
at 4 memories and 1,200 characters — roughly 300 tokens — and frequently injects nothing
(`DEFAULT_MAX_MEMORIES` / `DEFAULT_MAX_CHARS` in `core/injection.py`; see
[INJECTION_POLICY.md](development/INJECTION_POLICY.md)). Explicitly requested context is
larger by design: `visp-memory brief` defaults to a 2,000-token budget because you asked
for it. Neither of those tools publishes benchmarks or has an integrity story, so this is
a real difference in approach rather than marketing.

## What this project actually claims

Each row names the artifact that produces it. Every row except the first is a CI
script; the first is a command you run yourself, because a mining rate depends on
your history and no fixed number would be true of your repository.

| Claim | Evidence |
|---|---|
| Useful in the first minute, not after weeks | `visp-memory init` mines your existing git history at init time, in seconds. Reproduce it on your own repo with `./scripts/demo.sh` — the number it prints is the only one that matters to you. This row previously quoted "107 memories from 117 commits in under 5s" from an unnamed repository on an unnamed machine, which nothing reproduced. |
| Injects far less, far more precisely | 1.00 precision at ~15 tokens/task vs 0.09 at ~190 for naive retrieval ([BENCHMARK.md](BENCHMARK.md), `scripts/evaluate_oracle_gap.py`) |
| Knows when to say nothing | 0 false alarms on tasks no memory can help with; naive retrieval fired on 4 of 5 (same script) |
| Resists poisoned memory | 56.3% → 0% poisoned-retrieval, with 5/5 legitimate answers retained ([TRUST.md](TRUST.md), `scripts/evaluate_poisoning.py`) |
| Knows when its memories went stale | Memories anchored to deleted code are withheld (behavioural, not a rate) |

Every rate above is measured on a small authored corpus with no live model. They are
evidence that the selection policy behaves as specified. They are not evidence that
using this package improves the code an agent writes — see the exclusions below, which
are as much a part of the claim as the table is.

And what it does **not** claim:

- **No code-quality improvement.** The one controlled study of memory in coding agents
  found none; gains were in turns and tokens. Anyone claiming otherwise is ahead of the
  evidence.
- **No live-agent results.** The benchmarks measure selection quality, not issues
  resolved. That needs a live model on
  [SWE-Bench-CL](https://arxiv.org/pdf/2507.00014)-style task streams, which has not been
  run.
- **Recall is 0.625, not 1.00.** Abstaining costs 37.5% of the genuinely relevant
  memories — 3 of the 8. That is a deliberate trade, and it is a real cost.
- **Synthetic fixtures.** Both benchmarks use constructed corpora, not field data.

## When not to use this

- You want your assistant to remember your preferences across chats → mem0 or Letta.
- You need a hosted, enterprise-supported service → Zep.
- You work alone, on one machine, with one tool, and built-in memory is fine → keep it.
- You want maximum recall and are happy to pay for it in context → this will feel
  frustratingly quiet, by design.
