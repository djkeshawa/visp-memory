# Releasing

This is the single release procedure and checklist. Build details live in
[PACKAGING.md](PACKAGING.md); the executable source of the pipeline is
[build-release.yml](../../.github/workflows/build-release.yml).
Publication is an owner-authorized action. Preparing and testing artifacts does
not authorize tagging, pushing or publishing.

## Version and artifacts

Published versions are immutable. Check PyPI and GitHub for used versions before
choosing a new one. Update `pyproject.toml`, the fallback version in
`src/visp_memory/__init__.py`, and the project entry in `uv.lock`; use a matching
`vX.Y.Z` tag. Record the source commit and
artifact hashes; a version string alone is insufficient identity.

Release outputs include:

- `visp_memory-X.Y.Z-py3-none-any.whl` with the embedded dashboard.
- `visp_memory-X.Y.Z.tar.gz` source distribution.
- Platform standalone archives, with CLI/API/dashboard and no MCP.
- A GHCR container image.

## Before tagging

1. Inspect `git status --short`; the release commit must contain only intended
   changes. Check the version and migration implications.
2. Run `make release-check`. The
   [checker](../../scripts/release_check.py) runs Ruff, workflow validation,
   pytest, package/MCP import smoke, frontend and Python distribution builds,
   license/NOTICE verification, Twine metadata checks and a fresh-wheel smoke.
   Python distributions are built in a temporary directory and removed after the
   check; existing `dist/` artifacts are preserved and excluded from verification.
   `build` and `twine` must be installed; Twine is required unless explicitly
   skipped. A skipped check is not a complete release gate.
3. Keep security and repository-access checks in that gate:

   ```bash
   python3 -m pytest tests/server/test_auth.py tests/server/test_collaboration.py
   ```

4. Run dashboard checks with its existing lockfile:

   ```bash
   npm ci --prefix visp-memory-dashboard
   npm run lint --prefix visp-memory-dashboard
   npm run build --prefix visp-memory-dashboard
   npm audit --omit=dev --prefix visp-memory-dashboard
   ```

   Resolve production advisories or document the remaining risk and rationale.
   The release workflow's audit step must still pass.
5. Smoke CLI capture/recall in a disposable project. Exercise initialization and
   maintenance only against disposable data.
6. Smoke API/dashboard using the built distribution and disposable storage.
   Configure [authentication](AUTH.md); auth-disabled tests must stay on loopback.
   Verify server status at `/`, and dashboard pages `/dashboard`,
   `/dashboard/graph`, `/dashboard/recall` and `/dashboard/intents`.
7. Review [feature status](../FEATURE_STATUS.md), migration notes and benchmark
   limits. Do not relabel beta/frozen features or claim coding benefit from
   selection or integration tests.

For a packaged HTTP smoke, use
`python3 scripts/release_check.py --with-packaged-smoke`; add
`--with-browser-smoke` for the Chromium check or `--with-docker-build` for a
container build. These options require their respective local tooling.

## Publish after authorization

Commit only the intended changes, then create and push the matching tag after
authorization. Keep unrelated work and local configuration out of the release.
Use this procedure for version updates and publication; the former
`create-release.sh` helper has been retired.

```bash
git tag -a vX.Y.Z -m "Release X.Y.Z"
git push origin vX.Y.Z
```

A `v*` tag push starts the single release workflow. Quality gates precede builds;
Python artifacts are checked for metadata, license/NOTICE and dashboard assets.
PyPI publishing uses the built wheel/sdist and Trusted Publishing (OIDC).
The container and standalone jobs produce their own artifacts; GitHub Release
creation waits for those build jobs. Publication across services is not atomic.
A manual `workflow_dispatch` does not satisfy the PyPI tag-push publish condition.

### Trusted Publisher configuration

Verify these settings on the existing PyPI project and GitHub environment:

| Field | Value |
|---|---|
| PyPI project | `visp-memory` |
| GitHub owner | `djkeshawa` |
| Repository | `visp-memory` |
| Workflow | `build-release.yml` |
| Environment | `pypi` |

The workflow uses `id-token: write`; no persistent PyPI API token is required.
Environment approval rules, if configured, still apply. TestPyPI requires its
own publisher/environment configuration; it is not a second production path.

## After publication

1. Inspect every relevant Actions job and destination separately. A GitHub
   Release does not prove PyPI publication succeeded, or vice versa.
2. Download the released wheel and install it in a fresh virtual environment:

   ```bash
   python3 -m venv /tmp/visp-memory-release-smoke
   /tmp/visp-memory-release-smoke/bin/pip install "./visp_memory-X.Y.Z-py3-none-any.whl[api,mcp]"
   /tmp/visp-memory-release-smoke/bin/visp-memory --version
   ```

3. Verify dashboard assets and intended entry points from the installed artifact.
   Smoke standalone archives on their target platforms.
4. Ensure release notes include installation, feature status, known advisories,
   migration/data-safety notes and the exact artifact identity.

## Failure recovery

Inspect the failing job before retrying. Reproduce frontend errors with
`python3 build_frontend.py`, Python packaging errors with `python3 -m build`,
and artifact errors with the distribution verifier. Check OIDC settings for
publishing failures.

Do not delete or move a published tag, replace an existing version or describe a
partial publish as a rollback. The workflow's `skip-existing` allows already
uploaded PyPI files to be skipped on retry; it does not replace them. Changed
code or artifacts require a new version. Record partial destination failures and
recover through the authorized release procedure.
