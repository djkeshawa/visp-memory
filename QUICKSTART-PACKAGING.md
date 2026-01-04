# Quick Start: Packaging LLM Memory

This guide gets you started with packaging and distributing LLM Memory.

## Prerequisites

- Python 3.10+
- Node.js 20+
- Make (optional, for convenience)
- Docker (optional, for containers)

## Quick Build

### Option 1: Using Make (Recommended)

```bash
# Build everything (frontend + Python package)
make build

# Install locally
make install

# Test
llm-memory --version
uvicorn llm_memory.server.app:app --port 8000
# Visit http://localhost:8000/dashboard
```

### Option 2: Manual Build

```bash
# 1. Build frontend
python build_frontend.py

# 2. Build Python package
pip install build
python -m build

# 3. Install
pip install dist/*.whl[all]
```

## Distribution Methods

### 1. Python Package (pip install)

**Best for:** Python developers, pip distribution

```bash
# Build
make build

# Test locally
pip install dist/*.whl[all]

# Publish to PyPI
pip install twine
twine upload dist/*
```

Users install with:
```bash
pip install llm-memory[all]
```

### 2. Docker Container

**Best for:** Production deployment, cloud hosting

```bash
# Build
docker build -t llm-memory:latest .

# Run
docker run -p 8000:8000 llm-memory:latest

# Or with docker-compose
docker-compose up -d
```

Users pull with:
```bash
docker pull yourusername/llm-memory:latest
docker run -p 8000:8000 yourusername/llm-memory:latest
```

### 3. Standalone Executable

**Best for:** End users, no Python required

```bash
# Build (creates platform-specific binary)
./build_standalone.sh

# Distribute
dist/llm-memory-standalone-Linux-x86_64.tar.gz
```

Users extract and run:
```bash
tar -xzf llm-memory-standalone-*.tar.gz
cd llm-memory-standalone
./start-server.sh
```

## What Gets Packaged

All methods include:

✅ CLI tool (`llm-memory`)
✅ MCP server (`llm-memory-mcp`)
✅ FastAPI server
✅ Web dashboard (embedded)
✅ All Python dependencies

## File Sizes

- **Python wheel**: ~50KB + dependencies
- **Docker image**: ~500MB
- **Standalone**: ~200-500MB

## Testing Your Build

```bash
# Test CLI
llm-memory --version
llm-memory init --type code

# Test server
uvicorn llm_memory.server.app:app --port 8000

# Check dashboard
curl http://localhost:8000/
curl http://localhost:8000/dashboard
```

## Common Issues

**Frontend not building?**
```bash
cd llm-memory-dashboard
rm -rf node_modules .next out
npm install
npm run export
```

**Static files not found?**
```bash
# Rebuild and copy
python build_frontend.py
# Check location
ls -la src/llm_memory/server/static/
```

**Docker build slow?**
```bash
# Use BuildKit
DOCKER_BUILDKIT=1 docker build -t llm-memory:latest .
```

## Next Steps

1. **Read full documentation**: See [PACKAGING.md](PACKAGING.md)
2. **Set up CI/CD**: GitHub Actions workflow included in `.github/workflows/`
3. **Customize**: Edit configs in `pyproject.toml`, `Dockerfile`, `llm-memory.spec`
4. **Publish**: Follow PyPI/Docker Hub publishing guides

## Makefile Commands

```bash
make help              # Show all commands
make build             # Build everything
make build-frontend    # Build dashboard only
make build-python      # Build Python package only
make install           # Install locally
make install-dev       # Install with dev dependencies
make clean             # Remove build artifacts
make test              # Run tests
make docker-build      # Build Docker image
make docker-run        # Run Docker container
```

## Directory Structure After Build

```
llm-memory/
├── dist/
│   ├── llm_memory-0.1.0-py3-none-any.whl
│   ├── llm_memory-0.1.0.tar.gz
│   ├── llm-memory                          # Standalone binary
│   └── llm-memory-standalone-*.tar.gz
├── llm-memory-dashboard/
│   └── out/                                 # Next.js build
└── src/llm_memory/server/
    └── static/                              # Copied from out/
```

## Support

- Full docs: [PACKAGING.md](PACKAGING.md)
- Issues: https://github.com/yourusername/llm-memory/issues
- Documentation: [CLAUDE.md](CLAUDE.md)

## Quick Reference

| Task | Command |
|------|---------|
| Build all | `make build` |
| Install local | `make install` |
| Build Docker | `make docker-build` |
| Build standalone | `./build_standalone.sh` |
| Clean | `make clean` |
| Test | `make test` |
| Run server | `uvicorn llm_memory.server.app:app --port 8000` |
| Dashboard URL | `http://localhost:8000/dashboard` |
