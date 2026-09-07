# Testing

Use [CONTRIBUTING.md](../../CONTRIBUTING.md) for environment setup and contribution
rules. Tests should exercise observable behavior with isolated storage, not the
user's project database or paid providers.

## Required checks

Run focused tests for the change, then the repository checks before completion:

```bash
python3 -m pytest -q tests/core/test_memory.py
make test
make lint
```

`make test` runs the full pytest suite; `make lint` runs Ruff. Python dependencies
come from the project's existing extras. For debugging, add `-x -vv`, use `--lf`
to rerun failures or `--pdb` to inspect one. Counts and timings belong in run
receipts, not a permanently stale test-count claim in this guide.

## Test map

| Directory/file | Coverage |
|---|---|
| [tests/core/](../../tests/core) | Storage, Evidence, eligibility, ranking, briefs, intent and atomicity |
| [tests/capture/](../../tests/capture) | Git, test and conversation capture |
| [tests/cli/](../../tests/cli) | CLI workflows, output, exit codes and isolation |
| [tests/interfaces/](../../tests/interfaces) | MCP, profiles and protocol behavior |
| [tests/server/](../../tests/server) | API, authentication and repository access |
| [tests/recall/](../../tests/recall) | Proactive recall and guarded context |
| [tests/scripts/](../../tests/scripts) | Evaluators, benchmark preparation and verification |
| [tests/docs/](../../tests/docs) | Published benchmark figures compared with measured output |
| [tests/test_release_artifacts.py](../../tests/test_release_artifacts.py) | Distribution capabilities and required artifact contents |

Read [tests/conftest.py](../../tests/conftest.py) and the nearest existing fixture
before creating another one. Keep stores, configuration and working directories
in temporary locations; use noop/mock embeddings for deterministic local tests.
Scope records to a test repository and exercise actual capture/recall IDs.
Use async fixtures and awaited calls for async paths. Do not invent methods or
assert success merely because a result is nonempty.

## Regression expectations

Cover the failure trigger and the preserved behavior. For storage changes,
verify transaction rollback, Evidence integrity and capability refusals. For
recall changes, include ineligible records ahead of valid candidates, repository
boundaries, time/runtime scopes, quarantined content and abstention. For auth
changes, exercise read/write/admin permissions and inaccessible repositories.
Missing dependencies, collection errors and skipped integration checks must not
be reported as successful execution of the affected path.

Focused security and Evidence checks include:

```bash
python3 -m pytest -q tests/core/test_evidence_contract.py tests/core/test_memory_import_export_evidence.py
python3 -m pytest tests/server/test_auth.py tests/server/test_collaboration.py
```

Backend unit tests may mock a driver; they do not prove that a real service works.
Use the existing [CI workflow](../../.github/workflows/ci.yml) and
[Neo4j test Compose file](../../docker-compose.ci-neo4j.yml) for actual backend
contract configuration. Real tests cover capture, Evidence scope, rollback, corrections,
workflow ordering, dreaming undo, restart, and backup/migration recovery:

```bash
# Point only at disposable test databases; ADMIN_URI is erased by the admin fixture.
VISP_TEST_NEO4J_URI=bolt://127.0.0.1:17687 \
VISP_TEST_NEO4J_ADMIN_URI=bolt://127.0.0.1:27687 \
VISP_TEST_NEO4J_PASSWORD="$NEO4J_PASSWORD" \
python -m pytest tests/core/test_neo4j_integration.py -q
```

Without these environment variables, the relevant integration cases are skipped.
SQLite remains covered by the default full suite.

## Manual and packaged checks

Initialize a disposable project and test `decision`, `warn`, `recall`, `brief`, `preview` and `audit` with
known inputs. Maintenance commands should run only against disposable data.

For assistant integration, follow [MCP.md](MCP.md): install the integration,
check `doctor`, capture a known decision and retrieve it in a fresh session.
A hook subprocess passing is different from the host actually dispatching it.

For dashboard and release artifacts, use the
[release checklist](../deployment/RELEASING.md#before-tagging). It owns frontend,
packaged HTTP/browser smoke, metadata and license/NOTICE checks. Keep auth-enabled
access tests in the gate even if a local visual smoke disables auth on loopback.

## Evaluations and coverage

```bash
python3 scripts/evaluate_oracle_gap.py --json
python3 scripts/evaluate_poisoning.py --json
python3 scripts/evaluate_agent_ab.py --json
python3 scripts/evaluate_hallucination.py --json
python3 scripts/evaluate_memory_intelligence.py --json
python3 scripts/benchmark_memory.py --items 100 --json
python3 -m pytest --cov=visp_memory --cov-report=term-missing
```

Selection and poisoning results are documented in [BENCHMARK.md](../BENCHMARK.md). `evaluate_agent_ab.py` uses scripted responses, not a
live coding agent; its fixture success gap cannot establish coding benefit.
Performance timings depend on the machine. Use the benchmark script instead of
brittle elapsed-time assertions in unit tests.
