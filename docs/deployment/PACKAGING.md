# LLM Memory - Packaging & Distribution Guide

This document explains how to package and distribute LLM Memory with the integrated dashboard.

## Table of Contents

- [Overview](#overview)
- [Method 1: Python Package (pip)](#method-1-python-package-pip)
- [Method 2: Docker Container](#method-2-docker-container)
- [Method 3: Standalone Executable](#method-3-standalone-executable)
- [Development Builds](#development-builds)
- [Troubleshooting](#troubleshooting)

## Overview

LLM Memory can be distributed in three ways:

1. **Python Package** - Install via pip, includes embedded dashboard
2. **Docker Container** - Containerized deployment with all dependencies
3. **Standalone Executable** - Single binary with no dependencies required

All methods include the full functionality: CLI, API server, MCP server, and web dashboard.

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
- `dist/llm_memory-0.1.0-py3-none-any.whl` - Wheel distribution
- `dist/llm_memory-0.1.0.tar.gz` - Source distribution

### Installing

```bash
# Install from local wheel
pip install dist/llm_memory-0.1.0-py3-none-any.whl[all]

# Lean server install without local sentence-transformer model dependencies
pip install dist/llm_memory-0.1.0-py3-none-any.whl[api,mcp]

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
llm-memory init --type code
llm-memory record "test memory"

# Start server with dashboard
llm-memory serve
# Dashboard: http://localhost:8000/dashboard
# API docs: http://localhost:8000/docs
```

### File Structure

```
llm_memory/
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
docker build -t llm-memory:latest .
```

The Dockerfile uses a multi-stage build:
1. **Stage 1**: Build Next.js dashboard
2. **Stage 2**: Build Python package
3. **Stage 3**: Create minimal runtime image

### Running

```bash
# Basic run
docker run -p 8000:8000 llm-memory:latest

# With persistent storage
docker run -p 8000:8000 \
  -v ~/.llm-memory:/home/llmuser/.llm-memory \
  llm-memory:latest

# With environment variables
docker run -p 8000:8000 \
  -e NEO4J_URI=bolt://neo4j:7687 \
  -e NEO4J_PASSWORD=mypassword \
  llm-memory:latest
```

### Docker Compose

Create `docker-compose.yml`:

```yaml
version: '3.8'

services:
  llm-memory:
    image: llm-memory:latest
    ports:
      - "8000:8000"
    volumes:
      - ./data:/home/llmuser/.llm-memory
    environment:
      - LLM_MEMORY_STORAGE_BACKEND=neo4j
      - NEO4J_URI=bolt://neo4j:7687
      - NEO4J_PASSWORD=memorypass
    depends_on:
      - neo4j
    restart: unless-stopped

  neo4j:
    image: neo4j:5.15
    ports:
      - "7474:7474"
      - "7687:7687"
    volumes:
      - neo4j-data:/data
    environment:
      - NEO4J_AUTH=neo4j/memorypass
      - NEO4J_PLUGINS=["apoc"]
    restart: unless-stopped

volumes:
  neo4j-data:
```

Run with:
```bash
docker-compose up -d
```

### Registry Publishing

```bash
# Tag for registry
docker tag llm-memory:latest yourusername/llm-memory:0.1.0
docker tag llm-memory:latest yourusername/llm-memory:latest

# Push to Docker Hub
docker push yourusername/llm-memory:0.1.0
docker push yourusername/llm-memory:latest
```

## Method 3: Standalone Executable

Single-file executable with embedded Python runtime and dashboard. No dependencies required.

### Requirements

```bash
pip install pyinstaller
```

### Building

```bash
# Automated build script
./build_standalone.sh

# Manual build
python3 build_frontend.py
pyinstaller llm-memory.spec
```

This creates:
- `dist/llm-memory` - Single executable file
- `dist/llm-memory-standalone/` - Distribution package
- `dist/llm-memory-standalone-*.tar.gz` - Compressed archive

### Distribution Package Contents

```
llm-memory-standalone/
├── llm-memory              # Main executable
├── start-server.sh         # Convenience script
├── README-STANDALONE.md    # End-user documentation
├── README.md               # Full documentation
└── LICENSE
```

### Platform-Specific Builds

You must build on the target platform:

```bash
# Linux
./build_standalone.sh
# Creates: dist/llm-memory-standalone-Linux-x86_64.tar.gz

# macOS
./build_standalone.sh
# Creates: dist/llm-memory-standalone-Darwin-arm64.tar.gz

# Windows (in PowerShell)
python build_frontend.py
pyinstaller llm-memory.spec
```

### Usage

```bash
# Extract
tar -xzf llm-memory-standalone-Linux-x86_64.tar.gz
cd llm-memory-standalone

# Run CLI
./llm-memory --help
./llm-memory init --type code
./llm-memory record "test memory"

# Start server
./start-server.sh
# or manually:
./llm-memory server --port 8000
```

### Size Optimization

The standalone executable can be large (200-500MB). To reduce size:

1. **Use UPX compression** (already enabled in spec file):
   ```bash
   # Install UPX
   # Linux: apt-get install upx
   # macOS: brew install upx
   pyinstaller llm-memory.spec
   ```

2. **Exclude unnecessary packages**:
   Edit `llm-memory.spec` and add to `excludes`:
   ```python
   excludes=['matplotlib', 'scipy', 'pandas', ...]
   ```

3. **Strip debug symbols**:
   ```python
   # In llm-memory.spec
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
llm-memory serve --reload
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
llm-memory --version
python -c "from llm_memory.server.app import app; print('OK')"

# Test Docker
docker run --rm llm-memory:latest llm-memory --version

# Test standalone
dist/llm-memory-standalone/llm-memory --version
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
llm-memory-dashboard/out/
├── index.html
├── _next/
│   ├── static/
│   │   ├── chunks/
│   │   └── css/
│   └── ...
└── ...
```

This is copied to `src/llm_memory/server/static/` and served by FastAPI.

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
rm -rf llm-memory-dashboard/.next llm-memory-dashboard/node_modules
python3 build_frontend.py
```

### Static Files Not Found

**Problem**: Dashboard returns 404

**Solutions**:
1. Verify static files exist:
   ```bash
   ls -la src/llm_memory/server/static/
   ```

2. Rebuild frontend:
   ```bash
   python3 build_frontend.py
   ```

3. Check FastAPI logs for STATIC_DIR path

### PyInstaller Build Fails

**Problem**: Hidden imports missing

**Solutions**:
1. Add to `hiddenimports` in `llm-memory.spec`:
   ```python
   hiddenimports=[
       'missing_module',
       ...
   ]
   ```

2. Analyze imports:
   ```bash
   pyinstaller --debug=all llm-memory.spec
   ```

### Docker Build Slow

**Problem**: Build takes too long

**Solutions**:
1. Use BuildKit:
   ```bash
   DOCKER_BUILDKIT=1 docker build -t llm-memory:latest .
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
