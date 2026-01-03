# Contributing

Thanks for considering a contribution. This project is early, so the most valuable
contributions right now are bug reports from real repositories and evidence that the
injection policy is wrong.

## Before you start

- **Small fixes**: open a pull request directly.
- **Anything that changes behaviour**: open an issue first. The injection thresholds in
  particular are calibrated against measurements, not preference — see
  [docs/development/INJECTION_POLICY.md](docs/development/INJECTION_POLICY.md).
- **A feature that would widen scope**: check
  [docs/FEATURE_STATUS.md](docs/FEATURE_STATUS.md) first. Several areas are deliberately
  frozen, and a PR reviving one is likely to be declined on maintenance grounds rather
  than merit.

## Legal bits, done once

Two things are required before a pull request can be merged.

### 1. Sign off each commit (DCO)

Certify that you wrote the change, or have the right to submit it, by adding a
`Signed-off-by` line:

```bash
git commit -s -m "fix: handle empty anchor set"
```

That appends `Signed-off-by: Your Name <your@email.com>` using your git config. It
certifies the [Developer Certificate of Origin 1.1](https://developercertificate.org/),
reproduced in full below.

### 2. Sign the CLA (once, ever)

Comment on your first pull request:

```
I have read the CLA document and I hereby sign the CLA.
```

The [Contributor License Agreement](CLA.md) does **not** take your copyright — you keep
it. It grants the rights needed to distribute your work and keeps future relicensing
possible. Projects that skipped this step found the option closed permanently, because
every past contributor would have had to individually agree.

If you are contributing as part of your job, read §4.3 of the CLA carefully: employment
agreements commonly assign IP created during employment, including work done on personal
time.

## Development setup

```bash
git clone https://github.com/djkeshawa/visp-memory.git
cd visp-memory
pip install -e ".[api,mcp,capture,dev]"
```

## Before you open a pull request

```bash
pytest                                    # 637 tests, ~50s
ruff check .
python3 scripts/evaluate_oracle_gap.py    # must match docs/BENCHMARK.md
python3 scripts/evaluate_poisoning.py     # must match docs/TRUST.md
```

Both evaluation scripts run in CI. If your change moves those numbers, that is not
automatically a failure — but **update the published numbers in the docs in the same
pull request.** A stale benchmark claim is the one bug this project cannot afford, since
its whole position rests on numbers that hold up under checking.

If a change lowers injection precision to raise recall, say so explicitly in the PR
description and explain why the trade is worth it. On the current evidence it usually
is not.

## Style

Match the surrounding code. Comments should explain *why*, especially for anything
non-obvious — thresholds, ordering constraints, and guards against failure modes that
are not visible from the code alone. Several existing comments record bugs that were
found the hard way; that is deliberate.

---

## Developer Certificate of Origin 1.1

```
Developer Certificate of Origin
Version 1.1

Copyright (C) 2004, 2006 The Linux Foundation and its contributors.

Everyone is permitted to copy and distribute verbatim copies of this
license document, but changing it is not allowed.


Developer's Certificate of Origin 1.1

By making a contribution to this project, I certify that:

(a) The contribution was created in whole or in part by me and I
    have the right to submit it under the open source license
    indicated in the file; or

(b) The contribution is based upon previous work that, to the best
    of my knowledge, is covered under an appropriate open source
    license and I have the right under that license to submit that
    work with modifications, whether created in whole or in part
    by me, under the same open source license (unless I am
    permitted to submit under a different license), as indicated
    in the file; or

(c) The contribution was provided directly to me by some other
    person who certified (a), (b) or (c) and I have not modified
    it.

(d) I understand and agree that this project and the contribution
    are public and that a record of the contribution (including all
    personal information I submit with it, including my sign-off) is
    maintained indefinitely and may be redistributed consistent with
    this project or the open source license(s) involved.
```
