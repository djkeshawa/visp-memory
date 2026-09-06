# Visp Memory - Packaging & Distribution Guide

This document explains how to package and distribute Visp Memory with the integrated dashboard.

## Table of Contents

- [Overview](#overview)
- [Method 1: Python Package (pip)](#method-1-python-package-pip)
- [Method 2: Docker Container](#method-2-docker-container)
- [Method 3: Standalone Executable](#method-3-standalone-executable)
- [Development Builds](#development-builds)
- [Troubleshooting](#troubleshooting)

## Overview

Visp Memory can be distributed in three ways:

1. **Python Package** - Install via pip, includes embedded dashboard
2. **Docker Container** - Containerized deployment with all dependencies
3. **Standalone Executable** - CLI/API/dashboard binary with no Python dependency

| Distribution | CLI | API | Dashboard | MCP |
| --- | --- | --- | --- | --- |
| Python package with `[api,mcp]` | Yes | Yes | Release wheel | Yes |
| Docker container | Yes | Yes | Yes | Yes |
| Standalone executable | Yes | Yes | Yes | No |

MCP is intentionally available through the Python package and container, not
the standalone archive.

## Method 1: Python Package (pip)

This method bundles the Next.js dashboard as static files within the Python package.

### Building

```bash
# Step 1: Build the frontend
python3 build_frontend.py

# Step 2: Build the Python package
make build-python
# or manually:
pip install build
python -m build
```

This creates:
- `dist/visp_memory-<version>-py3-none-any.whl` - Wheel distribution
- `dist/visp_memory-<version>.tar.gz` - Source distribution

### Installing

```bash
# Install from local wheel
pip install "dist/visp_memory-<version>-py3-none-any.whl[all]"

# Lean server install without local sentence-transformer model dependencies
pip install "dist/visp_memory-<version>-py3-none-any.whl[api,mcp]"

# Or install directly from source
pip install -e ".[all]"
```

### Publishing to PyPI

```bash
# Install twine
pip install twine

# Upload to PyPI
twine upload dist/*

# Upload to Test PyPI (recommended first)
twine upload --repository testpypi dist/*
```

### Usage After Installation

```bash
# CLI commands work immediately
visp-memory init --type code
visp-memory record "test memory"

# Start server with dashboard
visp-memory serve
# Dashboard: http://localhost:8000/dashboard
# API docs: http://localhost:8000/docs
```

### File Structure

```
visp_memory/
├── core/
├── layers/
├── interfaces/
└── server/
    ├── app.py          # FastAPI server
    ├── schemas.py
    └── static/         # Embedded Next.js build
        ├── index.html
        ├── _next/
        └── ...
```

## Method 2: Docker Container

Use Docker Engine with the Compose plugin and BuildKit (Docker Desktop includes
both). Copy `.env.example` to `.env` only if you do not already have a local `.env`,
then set `VISP_MEMORY_BOOTSTRAP_ADMIN_PASSWORD` to a unique password. No cloud key is
required: `auto` uses an available embedding provider or falls back to keyword
search. See [AUTH.md](AUTH.md#dashboard-accounts) for account setup.

Choose one storage profile; both use the same host port:

```bash
docker compose --profile lite up -d --build --wait
# Or the frozen embedded graph backend:
docker compose --profile arcadedb up -d --build --wait
```

Open [the dashboard](http://127.0.0.1:8000/dashboard). `lite` persists SQLite in
`visp-memory-lite-data`; `arcadedb` persists its embedded store in
`visp-memory-arcadedb-data`. Existing `docker-compose.override.yml` volume mappings
continue to apply to these commands. `/readyz` checks storage and dashboard assets;
it does not certify that an embedding provider is connected.

Both profiles bind to `127.0.0.1:8000` by default. `VISP_MEMORY_PORT` changes the
host port. Remote exposure through `VISP_MEMORY_BIND_HOST=0.0.0.0` requires deliberate
authentication, TLS and network configuration.

### Local Ollama embeddings

The optional overlay configures the app's endpoint and model, starts Ollama, and
waits for the model pull to succeed before starting the app:

```bash
docker compose -f docker-compose.yml -f docker-compose.ollama.yml \
  --profile lite up -d --build --wait --wait-timeout 600
```

Replace `lite` with `arcadedb` for the embedded graph backend. Set
`VISP_MEMORY_OLLAMA_EMBEDDING_MODEL` to choose a different model; the pull job and app
use this same setting. The initial download needs network access and can take
several minutes. Models persist in `ollama-data`; Ollama has no published host port.
The overlay deliberately selects Ollama even if `.env` names a cloud provider.

**Existing local overrides:** explicit `-f` arguments disable automatic loading of
`docker-compose.override.yml`. If you already use an override (especially for a
custom data volume), preserve it by inserting `-f docker-compose.override.yml`
between the base file and Ollama overlay in every command. Omitting it can make the
app appear empty because it opens a different volume.

The base `ollama` profile remains available for operating the model service alone.
For an app plus Ollama, use the overlay so startup follows Compose's
[dependency conditions](https://docs.docker.com/compose/how-tos/startup-order/).
There is no `full` or Neo4j serving profile: Neo4j governed writes fail closed
pending schema-v3 Evidence support.

### Operate and rebuild

Use the same profiles and `-f` arguments for subsequent commands:

```bash
docker compose --profile lite ps
docker compose --profile lite logs --tail=100 -f
docker compose --profile lite stop
# Start again after pulling source changes; rebuild includes the dashboard:
docker compose --profile lite up -d --build --wait
```

`stop` and `down` preserve named volumes. `down --volumes` deletes the stored data;
do not use it for routine restarts or upgrades. Back up the store before upgrading.
For an Ollama download failure, inspect `logs ollama-pull`, resolve the connection or
model error, and rerun `up`. Check embedding connection status in Operations after
startup. Indexing existing memories after changing models is a separate operation.

The [Dockerfile](../../Dockerfile) builds the dashboard and wheel in separate stages.
Runtime dependencies come from `uv.lock`, with hashes checked and extras selected by
`VISP_MEMORY_EXTRAS`. Source changes reuse the dependency layer; download caches stay
in the builder. The final image runs as UID 1000 and contains no Node or uv build
tooling. Bind-mounted `/data` directories must be writable by UID 1000; named volumes
are initialized from the image automatically.

The default extras include API/MCP, ArcadeDB, the Neo4j driver and provider clients;
Chroma and local transformer/Torch dependencies remain optional. An embedding
provider alone does not enable a vector index on SQLite: include the `chroma` extra
for that backend when deliberately building a vector-enabled image. Override
`VISP_MEMORY_EXTRAS` only for a custom image and retain `api` for HTTP serving.
`make docker-build` builds locally; registry publishing belongs to the release
workflow.

To exercise the Docker configuration and build-context boundary:

```bash
VISP_TEST_DOCKER=1 python -m pytest tests/integration/deployment -q
# Also test a built image using disposable containers and a temporary volume:
VISP_TEST_DOCKER_IMAGE=visp-memory:local \
  python -m pytest tests/integration/deployment/test_docker_runtime.py -q
```

## Method 3: Standalone Executable

Single-file executable with embedded Python runtime and dashboard. No dependencies required.

### Requirements

```bash
pip install pyinstaller
```

PyInstaller freezes what is importable in the current environment, so the project
and every extra you want inside the binary must be installed first. `build_standalone.sh`
does this for you; a manual build must do it explicitly, or the executable builds
cleanly and then fails with `ModuleNotFoundError` on the user's machine.

The bundled set is `api,capture,analysis,chroma,neo4j`. MCP is deliberately
excluded and remains available through the Python package or container.
`local-embeddings` and `arcadedb` are also excluded: PyTorch pushes the archive
past the 2 GB GitHub release asset limit, and ArcadeDB needs a JVM the bundle
cannot carry. Override `BUNDLE_EXTRAS` only for a private custom build whose
runtime requirements you control.

### Building

```bash
# Automated build script (installs dependencies, builds, smoke tests, archives)
./build_standalone.sh

# Manual build
pip install -e ".[api,capture,analysis,chroma,neo4j]"
python3 build_frontend.py
pyinstaller visp-memory.spec
```

This creates:
- `dist/visp-memory` - Single executable file
- `dist/visp-memory-standalone/` - Distribution package
- `dist/visp-memory-standalone-*.tar.gz` - Compressed archive

### Distribution Package Contents

```
visp-memory-standalone/
├── visp-memory              # Main executable
├── start-server.sh          # Linux/macOS launcher
├── start-server.ps1         # Windows PowerShell launcher
├── README-STANDALONE.md     # Standalone capability and startup guide
├── README.md                # Full documentation
├── LICENSE
└── NOTICE                   # Required by Apache-2.0 section 4(d)
```

### Platform-Specific Builds

You must build on the target platform:

```bash
# Linux
./build_standalone.sh
# Creates: dist/visp-memory-standalone-Linux-x86_64.tar.gz

# macOS
./build_standalone.sh
# Creates: dist/visp-memory-standalone-Darwin-arm64.tar.gz

# Windows (in PowerShell)
pip install -e ".[api,capture,analysis,chroma,neo4j]"
python build_frontend.py
pyinstaller visp-memory.spec
```

### Usage

```bash
# Extract
tar -xzf visp-memory-standalone-Linux-x86_64.tar.gz
cd visp-memory-standalone

# Run CLI
./visp-memory --help
./visp-memory init --type code
./visp-memory record "test memory"

# Start server
./start-server.sh
# or manually:
./visp-memory serve --port 8000
```

### Size Optimization

The standalone executable can be large (200-500MB). To reduce size:

1. **Use UPX compression** (already enabled in spec file):
   ```bash
   # Install UPX
   # Linux: apt-get install upx
   # macOS: brew install upx
   pyinstaller visp-memory.spec
   ```

2. **Exclude unnecessary packages**:
   Edit `visp-memory.spec` and add to `excludes`:
   ```python
   excludes=['matplotlib', 'scipy', 'pandas', ...]
   ```

3. **Strip debug symbols**:
   ```python
   # In visp-memory.spec
   exe = EXE(..., strip=True, ...)
   ```

## Development Builds

For development and testing:

### Quick Local Build

```bash
# Install in editable mode
pip install -e ".[all]"

# Build frontend only
python3 build_frontend.py

# Test server
visp-memory serve --reload
```

### Using Makefile

```bash
# Build everything
make build

# Build components separately
make build-frontend
make build-python

# Install locally
make install

# Development install
make install-dev

# Clean artifacts
make clean

# Run tests
make test

# Format and lint
make format
make lint
```

### Testing Builds

```bash
# Test Python package
pip install dist/*.whl[all]
visp-memory --version
python -c "from visp_memory.server.app import app; print('OK')"

# Test Docker
docker run --rm visp-memory:latest visp-memory --version

# Test standalone
dist/visp-memory-standalone/visp-memory --version
```

## Frontend Build Details

### Next.js Static Export

The dashboard is built as a static export:

```javascript
// next.config.mjs
const nextConfig = {
  output: 'export',  // Static export mode
  distDir: 'out',    // Output directory
  images: {
    unoptimized: true,  // Required for static export
  },
}
```

### Build Output

```
visp-memory-dashboard/out/
├── index.html
├── _next/
│   ├── static/
│   │   ├── chunks/
│   │   └── css/
│   └── ...
└── ...
```

This is copied to `src/visp_memory/server/static/` and served by FastAPI.

### API Base URL Configuration

The dashboard should use relative URLs for API calls:

```typescript
// lib/api.ts
const API_BASE = process.env.NEXT_PUBLIC_API_BASE || '';

export async function fetchMemories() {
  const response = await fetch(`${API_BASE}/memories`);
  return response.json();
}
```

This works both in development (`http://localhost:3000` → `http://localhost:8000/api`) and production (same origin).

## Troubleshooting

### Frontend Build Fails

**Problem**: dashboard build fails

**Solutions**:
```bash
# Clean and rebuild
rm -rf visp-memory-dashboard/.next visp-memory-dashboard/node_modules
python3 build_frontend.py
```

### Static Files Not Found

**Problem**: Dashboard returns 404

**Solutions**:
1. Verify static files exist:
   ```bash
   ls -la src/visp_memory/server/static/
   ```

2. Rebuild frontend:
   ```bash
   python3 build_frontend.py
   ```

3. Check FastAPI logs for STATIC_DIR path

### PyInstaller Build Fails

**Problem**: Hidden imports missing

**Solutions**:
1. Add to `hiddenimports` in `visp-memory.spec`:
   ```python
   hiddenimports=[
       'missing_module',
       ...
   ]
   ```

2. Analyze imports:
   ```bash
   pyinstaller --debug=all visp-memory.spec
   ```

### Docker Build Slow

**Problem**: Build takes too long

**Solutions**:
1. Use BuildKit:
   ```bash
   DOCKER_BUILDKIT=1 docker build -t visp-memory:latest .
   ```

2. Cache npm packages:
   ```dockerfile
   # Add to Dockerfile
   RUN --mount=type=cache,target=/root/.npm npm ci
   ```

### Large Executable Size

**Problem**: Standalone executable is too large

**Solutions**:
1. Enable UPX compression (already in spec)
2. Exclude unnecessary packages
3. Use `--onefile` mode selectively
4. Consider splitting into multiple executables

## Size Comparison

Typical sizes for each distribution method:

- **Python Wheel**: ~50KB (code only) + dependencies
- **Docker Image**: ~500MB (includes Python runtime, dependencies, frontend)
- **Standalone Executable**: ~200-500MB (includes everything)
- **Source Distribution**: ~5MB (includes frontend source)

## Recommended Distribution

- **For Python developers**: Python package (pip)
- **For production deployment**: Docker container
- **For end users**: Standalone executable
- **For contributors**: Source distribution

## Next Steps

After packaging, consider:

1. **Documentation**: Update README with installation instructions
2. **CI/CD**: Automate builds with GitHub Actions
3. **Testing**: Set up integration tests for each distribution method
4. **Release**: Create GitHub releases with artifacts
5. **Monitoring**: Add telemetry to track usage

## Additional Resources

- [PyPI Publishing Guide](https://packaging.python.org/tutorials/packaging-projects/)
- [Docker Best Practices](https://docs.docker.com/develop/dev-best-practices/)
- [PyInstaller Documentation](https://pyinstaller.readthedocs.io/)
- [Next.js Static Export](https://nextjs.org/docs/app/building-your-application/deploying/static-exports)
