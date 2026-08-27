#!/bin/bash
# Build standalone executable for Visp Memory

set -e

PYTHON="${PYTHON:-python3}"

echo "================================"
echo "Visp Memory Standalone Builder"
echo "================================"

# Check if PyInstaller is installed
if ! "$PYTHON" -c "import PyInstaller" &> /dev/null; then
    echo "Installing PyInstaller..."
    "$PYTHON" -m pip install pyinstaller
fi

# Everything the bundled binary must be able to do offline. PyInstaller can only
# freeze what is importable in the current environment, so these have to be
# installed *before* the spec is analysed. `local-embeddings` and `arcadedb` are
# deliberately absent: the first drags in PyTorch (multi-GB, over the GitHub
# release asset limit) and the second needs a JVM on the user's machine, which
# defeats the point of a self-contained binary.
BUNDLE_EXTRAS="${BUNDLE_EXTRAS:-api,capture,analysis,chroma,neo4j}"

echo ""
echo "Step 1: Installing project and bundled dependencies (${BUNDLE_EXTRAS})..."
"$PYTHON" -m pip install -e ".[${BUNDLE_EXTRAS}]"

# Fail here rather than shipping a hollow binary. PyInstaller downgrades an
# unresolvable hidden import to a warning, so an environment missing these still
# produces an executable that dies with ModuleNotFoundError on first run.
# Only third-party distributions are probed. Importing visp_memory.server.app
# would be a stronger check, but it opens the caller's real memory database at
# module scope, so it fails on any machine whose store predates the current
# schema -- a build step must not depend on the developer's local data.
"$PYTHON" -c "import fastapi, uvicorn, typer, chromadb, neo4j, git, sklearn, visp_memory"

# Build frontend first
echo ""
echo "Step 2: Building frontend..."
"$PYTHON" build_frontend.py

# Build standalone executable
echo ""
echo "Step 3: Building standalone executable..."
"$PYTHON" -m PyInstaller visp-memory.spec

# Create distribution directory
echo ""
echo "Step 4: Creating distribution package..."
DIST_DIR="dist/visp-memory-standalone"
mkdir -p "$DIST_DIR"

# Copy executable
cp dist/visp-memory "$DIST_DIR/"

# Copy documentation. Apache-2.0 section 4(d) requires NOTICE to travel with the
# distribution, so it is not optional.
cp README.md "$DIST_DIR/"
cp LICENSE "$DIST_DIR/"
cp NOTICE "$DIST_DIR/"
cp standalone/README-STANDALONE.md "$DIST_DIR/"
cp standalone/start-server.sh "$DIST_DIR/"
cp standalone/start-server.ps1 "$DIST_DIR/"
chmod +x "$DIST_DIR/start-server.sh"
"$PYTHON" scripts/verify_distribution_artifacts.py "$DIST_DIR"

# Smoke test the frozen binary, not just the build environment.
#
# `--version` alone is not enough. A bundle can start fine and still be missing
# the dashboard, because the server only logs a warning and serves the API. So
# boot the real server and require /readyz, which returns 503 unless both storage
# and the dashboard assets resolve inside the bundle.
echo ""
echo "Step 5: Smoke testing the binary..."
"$DIST_DIR/visp-memory" --version

SMOKE_DIR=$(mktemp -d)
SMOKE_PORT="${SMOKE_PORT:-8788}"
SMOKE_PID=""
cleanup_smoke() {
    [ -n "$SMOKE_PID" ] && kill "$SMOKE_PID" 2>/dev/null || true
    rm -rf "$SMOKE_DIR"
}
trap cleanup_smoke EXIT

VISP_MEMORY_STORAGE_DATA_DIR="$SMOKE_DIR/data" \
VISP_MEMORY_SERVER_AUTH_ENABLED=false \
    "$DIST_DIR/visp-memory" serve --port "$SMOKE_PORT" > "$SMOKE_DIR/serve.log" 2>&1 &
SMOKE_PID=$!

smoke_ready=""
for _ in $(seq 1 60); do
    if curl -fsS -o /dev/null "http://127.0.0.1:${SMOKE_PORT}/readyz" 2>/dev/null; then
        smoke_ready="yes"
        break
    fi
    if ! kill -0 "$SMOKE_PID" 2>/dev/null; then
        break
    fi
    sleep 1
done

if [ -z "$smoke_ready" ]; then
    echo ""
    echo "ERROR: the bundled server never became ready. Server log:"
    cat "$SMOKE_DIR/serve.log"
    exit 1
fi

kill "$SMOKE_PID" 2>/dev/null || true
wait "$SMOKE_PID" 2>/dev/null || true
SMOKE_PID=""
echo "Binary starts, server boots, and the dashboard resolves inside the bundle."

# Create archive
echo ""
echo "Step 6: Creating archive..."
cd dist
tar -czf visp-memory-standalone-$(uname -s)-$(uname -m).tar.gz visp-memory-standalone/
cd ..
"$PYTHON" scripts/verify_distribution_artifacts.py "dist/visp-memory-standalone-$(uname -s)-$(uname -m).tar.gz"

echo ""
echo "================================"
echo "Build complete!"
echo "================================"
echo "Distribution: dist/visp-memory-standalone-$(uname -s)-$(uname -m).tar.gz"
echo ""
echo "To test:"
echo "  cd dist/visp-memory-standalone"
echo "  ./visp-memory --help"
