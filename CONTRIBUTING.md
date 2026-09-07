# Contributing

Bug reports, reproducible fixes, and documentation improvements are welcome.
Use a small pull request with a clear description and relevant verification.

## Before you start

- **Small fixes**: open a pull request directly.
- **Anything that changes behaviour**: open an issue first. The injection thresholds in
  particular are calibrated against measurements, not preference — see
  [injection policy](docs/development/ARCHITECTURE.md#injection-policy).
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
possible.

If you are contributing as part of your job, read §4.3 of the CLA carefully: employment
agreements commonly assign IP created during employment, including work done on personal
time.

## Development setup

```bash
git clone https://github.com/djkeshawa/visp-memory.git
cd visp-memory
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[api,mcp,capture,dev]"
```

On Windows, activate with `.venv\Scripts\Activate.ps1` in PowerShell. For the
dashboard, install its locked dependencies with `npm ci --prefix visp-memory-dashboard`.

## Before you open a pull request

```bash
make test
make lint
python3 scripts/evaluate_oracle_gap.py    # must match docs/BENCHMARK.md
python3 scripts/evaluate_poisoning.py     # must match docs/TRUST.md
```

Both evaluation scripts run in CI. If your change moves those numbers, that is not
automatically a failure — but **update the published numbers and their limitations in the same pull request.**

If a change lowers injection precision to raise recall, describe the measured tradeoff.

Testing conventions and integration checks are in the [testing guide](docs/development/TESTING.md).
Update the existing guide when documentation changes; keep the [documentation index](docs/README.md) as
the route to each topic instead of adding phase notes or duplicate checklists.

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
