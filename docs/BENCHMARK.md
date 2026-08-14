# Benchmark: selection quality

Reproduce everything here with:

```bash
python3 scripts/evaluate_oracle_gap.py
```

No network, no API key, deterministic. It runs in CI on every push, so the numbers below
cannot silently rot.

## What is being measured, and what is not

This measures **selection quality**: given a task, were the memories handed to the
assistant the right ones? It does **not** measure how many issues an agent then resolved.
Anyone claiming the latter needs a live model and a real task suite; this is the
cheap, honest part that can run on every commit.

The motivation comes from [SWE-ContextBench](https://arxiv.org/abs/2602.08316) (1,100
tasks, 51 repositories):

| Condition | Resolution |
|---|---|
| No context | 26.26% |
| Curated ("oracle") summaries | 34.34% |
| Freely self-retrieved context | 12.12 points below oracle, and more expensive than no context |

Curation is worth roughly 8 points; naive retrieval gives most of it back. This benchmark
asks how much of that gap a selection policy can close.

## Setup

10 memories from a plausible small service (auth, billing, migrations, uploads,
webhooks, feature flags). 10 tasks:

- **5 answerable** — a human labelled which memories are genuinely relevant.
- **3 unanswerable** — nothing stored can help (marketing CSS, i18n, iOS push).
- **2 vague** — `continue`, `fix it`. Real prompts are often this thin.

Including unanswerable and vague cases is the point. A benchmark made only of answerable
tasks cannot detect over-injection, which is the failure mode that matters.

Three strategies are compared over identical fixtures:

- `oracle` — exactly the labelled memories. Upper bound.
- `unfiltered_top8` — top 8 by score, no floor, no abstention. What "retrieve what looks
  related" actually does.
- `policy` — this project's precision-gated injection.

## Results

| Strategy | Precision | Recall | F1 | Mean tokens | Correct silence |
|---|---|---|---|---|---|
| oracle | 1.00 | 1.00 | 1.00 | 23.4 | 1.00 |
| unfiltered_top8 | 0.09 | 0.75 | 0.17 | 190.2 | 0.20 |
| **policy** | **1.00** | **0.62** | **0.77** | **14.9** | **1.00** |

**Oracle gap closed: 72%.**

- The policy never injected on a task no memory could help with (0 false alarms out of 5
  opportunities). Naive retrieval fired on 4 of 5.
- The policy spent **13× fewer tokens** than naive retrieval (14.9 vs 190.2 per case) and
  slightly fewer than the oracle itself, because it trims and budgets.

## What this costs — the negative result

**Policy recall is 0.62, not 1.00.** Precision bought by abstaining is paid for in recall:
across the answerable cases the policy left roughly a third of the genuinely relevant
memories on the floor, mostly to the 4-memory budget and the redundancy filter.

That trade is deliberate. On the evidence, a wrong injection costs more than a missing
one: unfiltered retrieval has *higher* recall (0.75) than the policy and is still far
worse overall, because its precision collapses to 0.09. But it is a real cost and should
not be hidden behind a precision number.

Two further honest caveats:

- **The fixtures are synthetic.** They are written to be separable by a human, which
  makes them easier than a real repository's history. Treat 72% as an upper-ish estimate
  of gap closure on clean data, not a field result.
- **Keyword mode only.** The benchmark runs with embeddings disabled for determinism,
  which is the default install but not the best configuration. Semantic recall should
  raise recall; that has not been measured here.

## What would make this stronger

Running the same policy against a live model on
[SWE-Bench-CL](https://arxiv.org/pdf/2507.00014)-style chronological task streams, where
memory accumulates across a repository's real issue history, and scoring turns, tokens,
and resolution rather than selection. The fixtures here are deliberately shaped to port
to that harness.

Until that exists, this page says what it can support and nothing more. Given that the
field's most-cited memory benchmark was
[audited and found to have 6.4% of its answer key wrong](https://penfieldlabs.substack.com/p/we-audited-locomo-64-of-the-answer),
and that a major vendor's headline score was
[publicly corrected downward by 26 points](https://github.com/getzep/zep-papers/issues/5),
under-claiming seems like the better long-term strategy.

---

# Benchmark: structurally conditioned recall

```bash
python3 scripts/evaluate_structural_recall.py
```

Same rules as above: no network, no API key, deterministic, run in CI on every push.

## The gap it addresses

Memory recalls by text, so it finds memories that *sound like* the task. Both of its
file-aware signals — `HybridRetriever._matches_entities` and `Memory._file_factor` — are
identity tests, and identity is zero at one structural hop. A memory recorded against
`core/ranking.py` scores exactly nothing for a task editing `core/hybrid_retrieval.py`,
which imports it, unless the words happen to overlap.

Memory now reads intel's consumer projection read-only, collapses it to the file grain
by the shared contract, and admits **at most three** memories per retrieval that are
attached to files within two import/test hops of the task's files, ranked strictly below
every identity match.

## Setup

Thirteen invented files with a realistic import graph — including two isolated files
and two test files — eighteen authored memories, and eight tasks carrying twenty-four
hand-labelled relevance judgements, each with a written reason. One labelled memory is
structurally unreachable, so a perfect structural signal still cannot score 1.0.

Every count in this paragraph is emitted by the script itself under `fixture` in
`--json` output; read it there rather than trusting this prose.

Three arms over identical fixtures:

- **A — no graph.** Today's behaviour.
- **B — the true graph.**
- **C — a misleading graph.** Same queries, same seeds, every adjacency rotated to a
  file that is genuinely not adjacent.

## Results

| Metric | A: no graph | B: true graph | C: misleading graph |
|---|---|---|---|
| recall@10 | 0.4688 | **0.7917** | 0.6250 |
| recall@5 | 0.4688 | 0.7292 | 0.5938 |
| precision@5 | 0.7812 | **0.6000** | 0.4875 |
| MRR | 0.8438 | 0.8438 | 0.8438 |
| mean results returned | 1.875 | 4.125 | 4.125 |
| admitted memories that were relevant | — | 9/18 (0.500) | 4/18 (0.222) |

Per-repository-shape split, which is a mandatory reporting line rather than a courtesy:

| Cohort | Tasks | recall@10 before | recall@10 after |
|---|---|---|---|
| Connected (seeds have neighbours) | 6 | 0.3750 | 0.8056 |
| Inert (seeds are isolated files) | 2 | 0.7500 | 0.7500 |

## The headline gap is not the mechanism's benefit

`B − A` is +0.3229. **That is not what the graph is worth.** This corpus has eighteen
memories over thirteen files, so a *randomly* adjacent memory is relevant often enough
that admitting any three raises recall on its own — which is exactly what arm C measures.
Split honestly:

- **+0.1562** — "any three extra memories". A wrong graph buys this.
- **+0.1667** — attributable to the adjacency being correct.

Admission precision separates the arms more cleanly than recall does: 0.500 with the true
graph against 0.222 with the rotated one.

## What this costs — the negative result

Precision@5 falls from 0.7812 to 0.6000. Three admissions per retrieval is three more
memories in front of a model, and a recall gain bought at an unreported cost is not a
gain. MRR does not move in any arm, which is the ranking floor working: no structural
admission can outrank an identity match.

With a fully wrong adjacency, precision@5 falls to 0.4875 and fourteen of eighteen
admissions are wasted — but recall never falls below arm A in any arm or on any single
task, because admissions are added to the result rather than swapped into it.

## Claim ceiling

This is a **synthetic authored corpus**. It shows the mechanism does what it was built to
do where the right answer is known. It is not a field result, it says nothing about any
real repository, and it is not comparable to `visp-kit`'s context-pack measurements —
different system, different corpus, different metric. Memory's claim this round is
conformance against intel's vectors plus this benchmark, and nothing beyond it.
