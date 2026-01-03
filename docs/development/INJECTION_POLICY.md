# Injection Policy

Why this project injects so little, and how the thresholds were chosen.

## The problem

The intuitive design for a memory system is: store everything, retrieve what looks
related, put it in the prompt. That design is measurably worse than having no memory.

[SWE-ContextBench](https://arxiv.org/abs/2602.08316) evaluated 1,100 software tasks
across 51 repositories and 9 languages:

| Condition | Resolution rate |
|---|---|
| No context | 26.26% |
| Agent-retrieved context, unfiltered | *below* the curated variant by 12.12 points |
| Curated ("oracle") summaries | 34.34% |

The paper's conclusion is blunt: "unfiltered or incorrectly selected context provides
limited or negative benefits", and both self-retrieval variants "are more expensive than
the no-context baseline". A separate controlled study of persistent memory in coding
agents found no code-quality gain at all, with benefits confined to turn and token
reduction on *complex* tasks, and net-negative value on trivial ones.

So the gap between "memory helps a lot" and "memory actively hurts" is entirely a
question of selection. That gap — roughly 12 points — is what this project is trying to
close.

## The rules

### 1. Abstain by default

Injecting nothing is a correct, frequent outcome. Recall returns candidates for a human
to glance at; injection spends the assistant's context and steers its behaviour. The two
deserve different bars, so injection is strictly more conservative than search.

Abstention triggers:

| Condition | Behaviour |
|---|---|
| No candidates | Inject nothing |
| Task has fewer than 3 meaningful terms *and* no file in scope | Inject nothing |
| Corpus below 5 memories | Warnings only |
| Nothing clears the relevance floor | Inject nothing |
| All candidates score alike | Inject nothing |

That last rule matters more than it looks. When every candidate has the same score, the
ranking contains no information and "the top result" is arbitrary — precisely the
unfiltered-context failure mode, dressed up as a ranked list.

### 2. Budget, do not fill

Defaults: **at most 4 memories, at most 1,200 characters** (~300 tokens). Session-start
briefs are capped at 900 characters; per-file injection at 700.

Before this policy existed, the session hook injected up to 4,000 characters of
whatever the store returned, truncated mid-sentence, on every session — roughly 1,400
tokens of unranked context before any work began.

Near-duplicate candidates are dropped: restating a memory already selected is the most
common way a budget is wasted.

### 3. Gate on complexity

Memory does not pay on trivial work. Two proxies are used: how specific the task is (a
concrete file path counts as strong signal) and how much history the store actually has.

A thin corpus restricts injection to warnings rather than disabling it, because a
hand-written warning is high-precision regardless of how little else is stored.

## How the thresholds were calibrated

`score_memory_result()` computes `similarity*0.50 + lexical*0.30 + importance*0.15 +
recency*0.05`. On the default install there is no embedding provider, so `similarity` is
the neutral constant 0.5 and `lexical` carries all the discrimination. Measured against a
real mined commit history, that maps the observable range to:

| Match quality | Score |
|---|---|
| No lexical overlap | ~0.40 |
| Half the query's terms | ~0.55 |
| Full lexical match | ~0.70 |

Hence `min_relevance = 0.55` — "at least half the query's terms are present". An earlier
value of 0.62 was chosen by intuition and would have rejected essentially every result
in keyword mode: safe, and useless.

Two related findings came out of the same measurement:

- Warning categories are `fragile_area`, `known_issue`, and `gotcha`. There is no
  category literally named `warning`, so a check for one silently never fired.
- Recall's default cut-off (0.56) sits just above the half-match band. A mined
  "historically fragile" warning scored 0.5549 and was invisible to `recall`. Injection
  therefore gathers candidates at a lower threshold (0.40) and applies its own policy,
  since a candidate that recall hides can never be reconsidered.

## Seeing what it decided

```bash
visp-memory preview "refactor the auth token flow" --file src/auth/tokens.py
```

Prints exactly what would be injected, or the reason nothing was — including how many
candidates were considered and why each was dropped.

## Changing the thresholds

Every decision is recorded on `InjectionResult`, so the policy is measurable rather than
asserted. Before loosening anything, run:

```bash
python3 scripts/evaluate_oracle_gap.py --json
```

and check that precision does not fall. A policy change that increases recall while
lowering precision is, on the evidence above, a regression.
