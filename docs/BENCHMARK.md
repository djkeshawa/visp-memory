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
