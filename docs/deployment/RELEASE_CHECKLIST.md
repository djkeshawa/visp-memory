# Release Checklist

Use this checklist before tagging a release. It is intentionally concrete so a
release is repeatable from a clean checkout.

## Before Tagging

1. Confirm the working tree only contains intended changes.

   ```bash
   git status --short
   ```

2. Run the local release gate.

   ```bash
   make release-check
   ```

   This runs:

   - `ruff check .`
   - `pytest -q`
   - MCP/package import smoke
   - `python3 build_frontend.py`
   - `python -m build --wheel`
   - `twine check dist/*` when `twine` is installed

   Security and scoped-access checks are mandatory release-gate coverage:

   ```bash
   python3 -m pytest tests/server/test_auth.py tests/server/test_collaboration.py
   ```

3. Check dependency advisories.

   ```bash
   npm audit --omit=dev --prefix llm-memory-dashboard
   ```

   Any remaining production dependency advisories must be fixed or explicitly
   documented in the release notes with a rationale.

4. Smoke the CLI from the checkout.

   ```bash
   LLM_MEMORY_EMBEDDING_PROVIDER=noop llm-memory --version
   LLM_MEMORY_EMBEDDING_PROVIDER=noop llm-memory init --type code
   LLM_MEMORY_EMBEDDING_PROVIDER=noop llm-memory record "release smoke"
   LLM_MEMORY_EMBEDDING_PROVIDER=noop llm-memory recall "release"
   ```

5. Smoke the dashboard/API.

   ```bash
   LLM_MEMORY_EMBEDDING_PROVIDER=noop \
   LLM_MEMORY_SERVER_AUTH_ENABLED=false \
   llm-memory serve
   ```

   Verify:

   - `http://127.0.0.1:8000/` returns server status.
   - `http://127.0.0.1:8000/dashboard` renders.
   - `/dashboard/graph`, `/dashboard/recall`, and `/dashboard/intents` render
     dashboard pages.

6. Review feature status labels in
   [development/MATURITY_PLAN.md](../development/MATURITY_PLAN.md). Do not
   describe beta or experimental features as stable in release notes.

## Tagging

Use `create-release.sh` or perform the manual process in
[RELEASING.md](RELEASING.md). Tags should use semantic versioning:

```bash
git tag -a v0.2.0 -m "Release 0.2.0"
git push origin v0.2.0
```

## After GitHub Actions

1. Confirm the release workflow passed.
2. Download the wheel from the release.
3. Install it in a fresh virtual environment.

   ```bash
   python3 -m venv /tmp/llm-memory-release-smoke
   /tmp/llm-memory-release-smoke/bin/pip install ./llm_memory-*.whl
   /tmp/llm-memory-release-smoke/bin/llm-memory --version
   ```

4. Confirm the release notes include:

   - Install command.
   - Stable/beta/experimental feature notes.
   - Known dependency advisories.
   - Migration or data safety notes when storage/config changes.
