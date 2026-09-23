# Benchmarks and limitations

These deterministic, authored fixtures measure memory selection and filtering.
They do not establish improved coding outcomes, production-scale performance,
or superiority over another product. Integration probes and scripted agent
responses are not substitutes for a controlled live coding comparison, and none
is reported here.

## Reproduce the measurements

From a development checkout:

```bash
python3 scripts/evaluate_oracle_gap.py --json
python3 scripts/evaluate_poisoning.py --json
python3 scripts/evaluate_memory_intelligence.py --json
python3 scripts/evaluate_structural_recall.py --json
python3 -m pytest tests/docs tests/scripts/test_evaluate_structural_recall.py -q
```

These checks use local fixtures without a provider key or live model.
[Published-figure tests](../../tests/docs/test_published_figures.py) compare the
selection and poisoning figures below with measured output.

## Selection

Ten authored memories cover a small service. Ten tasks include five answerable,
three unanswerable, and two vague requests. Human labels define relevant memories.
The oracle selects exactly those labels; unfiltered retrieval returns its top
eight candidates; the policy applies relevance, abstention, and output budgets.

| Strategy | Precision | Recall | F1 | Mean tokens | Correct silence |
|---|---|---|---|---|---|
| oracle | 1.00 | 1.00 | 1.00 | 23.4 | 1.00 |
| unfiltered_top8 | 0.12 | 1.00 | 0.22 | 190.0 | 0.20 |
| **policy** | **1.00** | **0.625** | **0.77** | **14.9** | **1.00** |

**Oracle gap closed: 70%.**

The policy spent **12.8× fewer tokens** than naive retrieval (14.9 versus 190.0
per case). This estimate describes injected text in the fixture.

The unfiltered baseline ranks lexical candidates for relevance before its limit,
so it finds all eight labelled memories across its 64 selections. It still injects
on four of the five tasks where silence is correct.

**Policy recall is 0.625, not 1.00.** The policy retrieved 5 of the 8 genuinely relevant
memories and left 3 on the floor — 37.5% of them. High precision has a measured
recall cost; the small corpus and keyword-only setup limit generalization.

## Poisoning resistance

The authored corpus contains 10 poisoned records and 100 benign records. Five
attack queries also have legitimate answers that should remain available.

| | Poisoned Retrieval Proportion |
|---|---|
| Undefended (relevance ranking only) | **69.57%** |
| Defended (provenance quarantine) | **0.00%** |
| Legitimate answers still injected | **5/5 (100%)** |

The undefended arm retrieves 16 poisoned records out of 23. The defended arm blocks
those records while retaining useful answers. Both properties are checked; simply
returning nothing is not considered a successful defense. These synthetic lures
do not establish resistance to every attack. See [trust boundaries](TRUST.md).

## Local intelligence

The local intelligence evaluator checks labelled recall hits, relationship evidence,
capture deduplication, a graph token budget, and stale-intent reporting. Its stable
measurements are recorded in [the baseline](MEMORY_INTELLIGENCE_BASELINE.json).

The graph trace contains four nodes and uses 108 of 120 estimated tokens (90%).
No graph items are omitted. This fixture does not establish better coding outcomes.

## Structurally conditioned recall

The structural experiment adds up to three memories attached to files within two
import/test hops, below direct identity matches. It compares a true graph with
both no graph and deliberately incorrect adjacency, so extra result volume can
be distinguished from useful structure.

### Setup

Thirteen invented files with a realistic import graph — including two isolated files
and two test files — eighteen authored memories, and eight tasks carrying twenty-four
hand-labelled relevance judgements, each with a written reason. One labelled memory is
structurally unreachable, so a perfect structural signal still cannot score 1.0.
The script emits these counts under `fixture` in its `--json` output.

Three arms over identical fixtures:

- **A — no graph.** Text and identity matches only.
- **B — the true graph.**
- **C — a misleading graph.** Same queries, same seeds, every adjacency rotated to a
  file that is genuinely not adjacent.

### Results

| Metric | A: no graph | B: true graph | C: misleading graph |
|---|---|---|---|
| recall@10 | 0.4688 | **0.7917** | 0.6250 |
| recall@5 | 0.4688 | 0.7292 | 0.5938 |
| precision@5 | 0.7812 | **0.6000** | 0.4875 |
| MRR | 0.8438 | 0.8438 | 0.8438 |
| mean results returned | 1.875 | 4.125 | 4.125 |
| admitted memories that were relevant | — | 9/18 (0.500) | 4/18 (0.222) |

| Cohort | Tasks | recall@10 before | recall@10 after |
|---|---|---|---|
| Connected (seeds have neighbours) | 6 | 0.3750 | 0.8056 |
| Inert (seeds are isolated files) | 2 | 0.7500 | 0.7500 |

### Interpretation

`B − A` is +0.3229, but that is not what the graph is worth. With eighteen memories
over thirteen files, admitting any three extra memories raises recall on its own,
which arm C measures:

- **+0.1562** comes from "any three extra memories"; a wrong graph buys this.
- **+0.1667** is attributable to the adjacency being correct.

Admission precision separates the arms more cleanly: 0.500 with the true graph
against 0.222 with the rotated one.

The cost is real. Precision@5 falls from 0.7812 to 0.6000 because three more
memories reach the model. MRR does not move in any arm: no structural admission
can outrank an identity match. With a fully wrong adjacency, precision@5 falls to
0.4875 and fourteen of eighteen admissions are wasted, but recall never falls
below arm A, because admissions are added to the result rather than swapped in.

## Scope

All corpora are synthetic with known labels. These are mechanism checks, not
field results. Results from other products or broader context-pack systems are
not interchangeable with these measurements.
