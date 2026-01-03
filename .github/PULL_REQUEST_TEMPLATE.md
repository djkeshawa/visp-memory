## What this changes

<!-- One or two sentences. Link the issue if there is one. -->

## Why

<!-- What problem does this solve? For behaviour changes, what evidence supports it? -->

## Checklist

- [ ] `pytest` passes
- [ ] `ruff check .` passes
- [ ] Commits are signed off (`git commit -s`) — see [CONTRIBUTING.md](../CONTRIBUTING.md)
- [ ] I have signed the [CLA](../CLA.md) (first-time contributors only — comment
      `I have read the CLA document and I hereby sign the CLA.` below)

## If this changes recall, ranking, or injection

- [ ] `python3 scripts/evaluate_oracle_gap.py` — numbers recorded below
- [ ] `python3 scripts/evaluate_poisoning.py` — numbers recorded below
- [ ] Published numbers in `docs/BENCHMARK.md` / `docs/TRUST.md` updated to match

<!--
Paste the before/after numbers here. A change that raises recall by lowering
precision needs an explicit justification — on the current evidence that trade
is usually a regression.
-->
