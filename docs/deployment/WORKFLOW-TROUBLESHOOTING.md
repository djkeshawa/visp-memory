# GitHub Workflow Troubleshooting

## Common Issues and Fixes

### Issue 1: "python: command not found"

**Error:**
```
/bin/bash: line 1: python: command not found
```

**Fix:** The workflow uses `python` but should use `python3`

Check these files:
- `build_frontend.py` - shebang should be `#!/usr/bin/env python3`
- Workflow steps should use `python3` not `python`

**Quick fix:**
```bash
# Update workflow file
sed -i 's/python build_frontend.py/python3 build_frontend.py/g' .github/workflows/release-simple.yml
sed -i 's/python -m build/python3 -m build/g' .github/workflows/release-simple.yml
```

### Issue 2: "npm: command not found" or npm failures

**Error:**
```
npm: command not found
```

**Fix:** Node.js setup issue

Add to workflow before npm commands:
```yaml
- name: Set up Node.js
  uses: actions/setup-node@v4
  with:
    node-version: '20'
    cache: 'npm'
    cache-dependency-path: 'llm-memory-dashboard/package-lock.json'
```

### Issue 3: "Permission denied" on scripts

**Error:**
```
Permission denied: ./build_frontend.py
```

**Fix:** Scripts not executable

Either:
1. Make them executable locally and commit:
   ```bash
   chmod +x build_frontend.py create-release.sh
   git add -u
   git commit -m "fix: make scripts executable"
   ```

2. Or run with interpreter in workflow:
   ```yaml
   - name: Build Frontend
     run: python3 build_frontend.py
   ```

### Issue 4: "No module named 'build'"

**Error:**
```
ModuleNotFoundError: No module named 'build'
```

**Fix:** Need to install build package first

Add before build step:
```yaml
- name: Install build tools
  run: python3 -m pip install --upgrade pip build
```

### Issue 5: Frontend build fails - "package-lock.json not found"

**Error:**
```
npm ERR! Could not read package-lock.json
```

**Fix:** Missing package-lock.json

Either:
1. Commit package-lock.json:
   ```bash
   cd llm-memory-dashboard
   npm install
   git add package-lock.json
   git commit -m "chore: add package-lock.json"
   ```

2. Or use `npm install` instead of `npm ci` in workflow:
   ```yaml
   - name: Install frontend dependencies
     run: |
       cd llm-memory-dashboard
       npm install
   ```

### Issue 6: "Static files not found" in build

**Error:**
```
FileNotFoundError: src/llm_memory/server/static
```

**Fix:** Frontend not built before Python package

Ensure workflow order:
```yaml
- name: Build Frontend
  run: python3 build_frontend.py

- name: Build Python Package  # AFTER frontend
  run: python3 -m build --wheel
```

### Issue 7: Workflow not triggering

**Symptoms:** Push tag but workflow doesn't run

**Fixes:**

1. Check Actions are enabled:
   - Settings → Actions → General
   - Set to "Allow all actions and reusable workflows"

2. Check tag format:
   ```bash
   # Workflow triggers on tags starting with 'v'
   git tag -a v0.1.0 -m "Release"  # ✓ Works
   git tag -a 0.1.0 -m "Release"   # ✗ Won't trigger
   ```

3. Push the tag:
   ```bash
   git push origin v0.1.0  # Must push the tag!
   ```

### Issue 8: "refusing to allow a GitHub App to create or update workflow"

**Error:**
```
refusing to allow a GitHub App to create or update workflow
```

**Fix:** Permissions issue

Settings → Actions → General → Workflow permissions:
- Select "Read and write permissions"
- Check "Allow GitHub Actions to create and approve pull requests"

### Issue 9: Release creation fails

**Error:**
```
Resource not accessible by integration
```

**Fix:** Missing permissions

Add to workflow:
```yaml
jobs:
  build-and-release:
    permissions:
      contents: write  # Required for creating releases
```

### Issue 10: "GITHUB_TOKEN" invalid

**Fix:** Use the automatic token

Workflow should have:
```yaml
env:
  GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

No need to create a personal token - GitHub provides this automatically.

## How to Debug

### 1. Check the Logs

GitHub Actions → Click failed run → Click failed step → Read error

### 2. Test Locally First

Before pushing:
```bash
# Test frontend build
python3 build_frontend.py

# Test Python build
python3 -m build --wheel

# Verify
ls -la dist/
ls -la src/llm_memory/server/static/
```

### 3. Enable Debug Logging

Add to workflow (top level):
```yaml
env:
  ACTIONS_STEP_DEBUG: true
  ACTIONS_RUNNER_DEBUG: true
```

### 4. Test with workflow_dispatch

Trigger manually to test:
```yaml
on:
  workflow_dispatch:  # Allows manual trigger
  push:
    tags:
      - 'v*'
```

Then: Actions → Select workflow → Run workflow

## Quick Fixes Summary

| Error | Quick Fix |
|-------|----------|
| `python: command not found` | Use `python3` instead |
| `npm: command not found` | Add Node.js setup step |
| `Permission denied` | `chmod +x` or use interpreter |
| `No module 'build'` | `pip install build` |
| `package-lock.json not found` | Use `npm install` not `npm ci` |
| Workflow not triggering | Check Actions enabled, tag format |
| Release creation fails | Add `permissions: contents: write` |

## Still Stuck?

1. Share the error log
2. Check if local build works: `make build`
3. Try manual release first to isolate issue
4. Check GitHub Actions status page

## Working Workflow Template

Here's a minimal working version:

```yaml
name: Release

on:
  push:
    tags:
      - 'v*'

jobs:
  release:
    runs-on: ubuntu-latest
    permissions:
      contents: write

    steps:
      - uses: actions/checkout@v4

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Setup Node
        uses: actions/setup-node@v4
        with:
          node-version: '20'

      - name: Build Frontend
        run: python3 build_frontend.py

      - name: Build Package
        run: |
          python3 -m pip install build
          python3 -m build --wheel

      - name: Create Release
        uses: softprops/action-gh-release@v2
        with:
          files: dist/*.whl
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```
