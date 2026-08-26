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

Fully containerized deployment with multi-stage build.

### Building

```bash
# Build the image
make docker-build
# or manually:
docker build -t visp-memory:latest .
```

The Dockerfile uses a multi-stage build:
1. **Stage 1**: Build Next.js dashboard
2. **Stage 2**: Build Python package
3. **Stage 3**: Create minimal runtime image

### Running

```bash
# Basic run
docker run -p 8000:8000 visp-memory:latest

# With persistent storage
docker run -p 8000:8000 \
  -v ~/.visp-memory:/data \
  visp-memory:latest

# With environment variables
docker run -p 8000:8000 \
  -e NEO4J_URI=bolt://neo4j:7687 \
  -e NEO4J_PASSWORD=mypassword \
  visp-memory:latest

# With embedded ArcadeDB graph storage
docker run -p 8000:8000 \
  -e VISP_MEMORY_STORAGE_BACKEND=arcadedb \
  -v ~/.visp-memory:/data \
  visp-memory:latest
```

### Docker Compose

The repository includes `docker-compose.yml` with three deployment profiles:

```bash
# SQLite-backed local deployment; this remains the default lite profile
docker compose --profile lite up --build

# Embedded ArcadeDB graph storage; no separate database service
docker compose --profile arcadedb up --build

# Full graph deployment with Neo4j included
docker compose --profile full up --build
```

All app profiles expose the API/dashboard at `http://localhost:8000/dashboard`.
The ArcadeDB profile runs the embedded graph backend inside the Visp Memory app
container, stores data in the `visp-memory-arcadedb-data` volume, and does not
start an external ArcadeDB service.

The full profile starts Neo4j 5 plus Ollama, pulls `nomic-embed-text`, and
configures `VISP_MEMORY_STORAGE_BACKEND=neo4j`, `NEO4J_URI=bolt://neo4j:7687`,
`OLLAMA_HOST=http://ollama:11434`, and persistent volumes automatically.
Override `NEO4J_PASSWORD`, `VISP_MEMORY_PORT`, or `VISP_MEMORY_REPO_ID` in your
shell or `.env` file.

The default image includes API, MCP, ArcadeDB Embedded, Neo4j driver, OpenAI
embeddings, and Ollama embeddings. Override `VISP_MEMORY_EXTRAS` when you want a
leaner image, for example to exclude ArcadeDB from sqlite-only deployments.
`VISP_MEMORY_EMBEDDING_PROVIDER=auto` prefers OpenAI when
`OPENAI_API_KEY` or `EMBEDDING_API_KEY` is set, then Ollama when `OLLAMA_HOST`
is available. Local sentence-transformer embeddings are optional because they
add large model/runtime dependencies:

```bash
VISP_MEMORY_EXTRAS=api,mcp,neo4j,local-embeddings \
VISP_MEMORY_EMBEDDING_PROVIDER=sentence-transformers \
docker compose --profile full up --build
```

### Registry Publishing

```bash
# Tag for registry
docker tag visp-memory:latest djkeshawa/visp-memory:0.1.0
docker tag visp-memory:latest djkeshawa/visp-memory:latest

# Push to Docker Hub
docker push djkeshawa/visp-memory:0.1.0
docker push djkeshawa/visp-memory:latest
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
