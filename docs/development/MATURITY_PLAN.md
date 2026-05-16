# LLM Memory Maturity Plan

This document defines what "mature" means for LLM Memory and the order to get
there. The goal is not to add more surface area first; it is to make the current
CLI, MCP server, API, dashboard, and storage paths reliable enough for real use.

## Maturity Definition

LLM Memory is mature when a developer can:

- Install it from a release without cloning the repo.
- Start with local SQLite storage in under five minutes.
- Connect an MCP-compatible assistant and use memory tools without custom glue.
- Run the API/dashboard with documented auth and storage settings.
- Trust that tests, lint, packaging, and dashboard build gates are green.
- Understand which features are stable, beta, or experimental.
- Upgrade without losing memory data.

## Release Readiness Levels

### Level 1: Local Developer Preview

Target user: one developer using the CLI and MCP server locally.

Required:

- Accurate quick start for `pip install -e ".[all]"`, `llm-memory init`,
  `llm-memory serve`, and `llm-memory-mcp`.
- SQLite/local storage documented as the default path.
- MCP configuration example documented against the installed console script.
- Full `pytest` and `ruff check .` pass through `make release-check`.
- Dashboard build script works without network-only font dependencies.
- Known dependency advisories documented.

### Level 2: Team Beta

Target user: a small team sharing a central server.

Required:

- Auth story documented end to end in `docs/deployment/AUTH.md`: API keys,
  JWT secret, anonymous mode, and local development mode.
- Neo4j setup documented as the team storage path, not the local default.
- Migration/export/import flow documented and tested for local backups.
- Docker run instructions verified.
- Dashboard smoke checks automated through `scripts/smoke_packaged_dashboard.py`.
- Docker image build verified with a lean runtime dependency profile.
- MCP tools documented with input/output examples.

### Level 3: Production Candidate

Target user: teams depending on memory across repositories.

Required:

- Versioned configuration schema and migration notes.
- Stable REST API contract with compatibility expectations.
- Storage health checks and actionable startup errors.
- CI gates for Python tests, Ruff, dashboard build, and package build.
- Optional local gates for packaged dashboard browser smoke and Docker build.
- Release artifacts produced from a reproducible workflow.
- Security posture reviewed: auth defaults, CORS, token handling, and stored
  sensitive data guidance.

## Current Priority Order

1. Stabilize installation, quick start, and docs.
2. Keep quality gates green: `ruff check .`, `pytest`, frontend build, package
   build.
3. Verify MCP server behavior from an installed package.
4. Add a documented release checklist and make packaging repeatable.
5. Add team-server hardening: auth examples, Neo4j verification, Docker smoke.
6. Add upgrade/data safety docs: backup, export/import, config migration.
7. Automate browser/dashboard smoke tests.

## Feature Status Labels

Use these labels in docs and release notes:

- Stable: expected to work for normal use and covered by tests.
- Beta: useful, but needs more live integration testing.
- Experimental: available for exploration, not guaranteed for workflows.

Suggested current classification:

- Stable: CLI basics, local SQLite storage, memory record/recall/context,
  FastAPI basic routes.
- Beta: dashboard, MCP server, repository/team APIs, remote storage.
- Experimental: conversation capture, conflict detection, advanced analysis,
  full Neo4j production operation.

## Non-Goals For The Next Pass

- Do not add new AI features until setup and release confidence are stronger.
- Do not make Neo4j mandatory for local users.
- Do not introduce a new framework or service unless it removes a concrete
  operational risk.
