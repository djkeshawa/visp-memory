# How to Create a Release

This guide explains how to create and publish new releases of LLM Memory.

## Overview

When you create a release:
1. A GitHub Release is automatically created
2. The wheel file (`.whl`) is built with the embedded dashboard
3. Users can download and install directly from the release page

## Quick Release (Recommended)

Use the automated script:

```bash
./create-release.sh
```

This will:
1. Ask for the new version number
2. Update `pyproject.toml`
3. Create a git tag
4. Push to GitHub
5. Trigger automatic build and release

Then wait ~3-5 minutes for GitHub Actions to:
- Build the frontend
- Create the Python wheel
- Publish the GitHub Release
- Attach the wheel file

## Manual Release Process

If you prefer manual control:

### 1. Update Version

Edit `pyproject.toml`:

```toml
[project]
name = "llm-memory"
version = "0.2.0"  # Update this
```

### 2. Commit Changes

```bash
git add pyproject.toml
git commit -m "chore: bump version to 0.2.0"
git push
```

### 3. Create and Push Tag

```bash
# Create annotated tag
git tag -a v0.2.0 -m "Release 0.2.0"

# Push tag to trigger release workflow
git push origin v0.2.0
```

### 4. Wait for GitHub Actions

The tag push triggers `.github/workflows/build-release.yml`, which will:
- Build the frontend (Next.js)
- Create the Python wheel with embedded frontend
- Create a GitHub Release
- Upload the wheel file as an asset

(`.github/workflows/release-simple.yml` is a manual, wheel-only path triggered
via "Run workflow" / `workflow_dispatch`; it does **not** run on tag push.)

Check progress at: `https://github.com/yourusername/llm-memory/actions`

### 5. Review the Release

Once complete, the release appears at:
`https://github.com/yourusername/llm-memory/releases`

It includes:
- Download link for the wheel file
- Auto-generated release notes
- Installation instructions
- Changelog since last version

## What Gets Built

Each release includes a single wheel file:
- **Name**: `llm_memory-X.Y.Z-py3-none-any.whl`
- **Size**: ~600 KB
- **Contains**:
  - All Python code
  - Embedded web dashboard (Next.js build)
  - Configuration files
  - Entry points for CLI and MCP server

## Version Numbering

Follow [Semantic Versioning](https://semver.org/):

- **X.0.0** - Major version (breaking changes)
- **0.X.0** - Minor version (new features, backwards compatible)
- **0.0.X** - Patch version (bug fixes)

Examples:
- `v0.1.0` - Initial release
- `v0.1.1` - Bug fix
- `v0.2.0` - New features added
- `v1.0.0` - First stable release
- `v0.2.0-beta` - Pre-release version

## Pre-release Versions

For beta/alpha releases:

```bash
# Create pre-release tag
git tag -a v0.2.0-beta -m "Beta release 0.2.0"
git push origin v0.2.0-beta
```

GitHub will automatically mark it as "Pre-release" if the tag contains:
- `alpha`
- `beta`
- `rc` (release candidate)

## Testing Before Release

Always test the build locally first:

```bash
# Clean previous builds
make clean

# Run the local release gate
make release-check

# Start server and check dashboard manually
LLM_MEMORY_EMBEDDING_PROVIDER=noop \
LLM_MEMORY_SERVER_AUTH_ENABLED=false \
llm-memory serve
# Visit http://127.0.0.1:8000/dashboard
```

For the full manual checklist, see [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md).

## GitHub Actions Workflow

The tag-triggered release workflow is `.github/workflows/build-release.yml`. It runs when:
- A tag starting with `v` is pushed (e.g., `v0.1.0`)

A separate workflow, `.github/workflows/release-simple.yml`, is a manual,
wheel-only path triggered via the "Run workflow" button (`workflow_dispatch`)
and does **not** run on tag push.

**What it does:**

1. **Setup** (1 min)
   - Checkout code
   - Setup Python 3.11
   - Setup Node.js 20

2. **Build Frontend** (2 min)
   - Run `python3 build_frontend.py`
   - Install npm dependencies
   - Build the static dashboard
   - Copy static files to the package directory

3. **Build Python Package** (1 min)
   - Install build tools
   - Create wheel with embedded frontend
   - Verify package imports correctly

4. **Create Release** (1 min)
   - Generate release notes
   - Create GitHub Release
   - Upload wheel file

**Total time:** ~5 minutes

## Troubleshooting

### Workflow fails on "Build Frontend"

**Problem:** npm dependencies failed to install

**Solution:**
```bash
# Test locally
python3 build_frontend.py
```

Fix any errors, commit, and push again.

### Workflow fails on "Build Python Package"

**Problem:** Package build failed

**Solution:**
```bash
# Test locally
python3 -m build --wheel
```

Check `pyproject.toml` for syntax errors.

### Release created but wheel file is missing

**Problem:** Upload artifact step failed

**Solution:** Check the workflow logs. The build may have succeeded but upload failed. You can manually upload the wheel from the artifacts.

### Tag already exists

**Problem:** You're trying to re-release the same version

**Solution:**
```bash
# Delete local tag
git tag -d v0.1.0

# Delete remote tag
git push origin :refs/tags/v0.1.0

# Create new tag
git tag -a v0.1.0 -m "Release 0.1.0"
git push origin v0.1.0
```

**Warning:** Only do this if the release hasn't been published yet!

## Publishing to PyPI (Optional)

After the GitHub release is created, you can optionally publish to PyPI:

### 1. Get API Token

1. Go to https://pypi.org/manage/account/token/
2. Create a new API token
3. Save it as `PYPI_TOKEN` in GitHub Secrets

### 2. Upload Manually

```bash
# Download the wheel from GitHub release
wget https://github.com/yourusername/llm-memory/releases/download/v0.1.0/llm_memory-0.1.0-py3-none-any.whl

# Upload to PyPI
pip install twine
twine upload llm_memory-0.1.0-py3-none-any.whl
```

### 3. Or Add to Workflow

Add to `.github/workflows/release-simple.yml`:

```yaml
- name: Publish to PyPI
  if: github.event_name == 'push' && startsWith(github.ref, 'refs/tags/')
  env:
    TWINE_USERNAME: __token__
    TWINE_PASSWORD: ${{ secrets.PYPI_TOKEN }}
  run: |
    pip install twine
    twine upload dist/*.whl
```

Then users can install with:
```bash
pip install llm-memory
```

## Release Checklist

Use [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md) as the source of truth.

## Alternative: Manual GitHub Release

If the workflow isn't set up or you prefer manual control:

1. **Build locally:**
   ```bash
   make build
   ```

2. **Go to GitHub Releases:**
   `https://github.com/yourusername/llm-memory/releases/new`

3. **Fill in details:**
   - Tag: `v0.1.0`
   - Title: `LLM Memory v0.1.0`
   - Description: (copy from the workflow's changelog template)

4. **Upload wheel:**
   - Drag `dist/llm_memory-0.1.0-py3-none-any.whl` to the assets section

5. **Publish release**

This gives you full control but takes more time.

## Advanced: Automated PyPI Publishing

To fully automate PyPI publishing on release:

1. Add PyPI token to GitHub secrets
2. Enable "Trusted Publisher" on PyPI
3. Update workflow to include PyPI upload step

See [PACKAGING.md](PACKAGING.md) for detailed instructions.

## Getting Help

- **Workflow not running?** Check `.github/workflows/` permissions
- **Build failing?** Check GitHub Actions logs for detailed errors
- **Need to rollback?** Delete the release and tag, fix issues, re-release

---

**Next:** See [INSTALL.md](INSTALL.md) for user installation instructions.
