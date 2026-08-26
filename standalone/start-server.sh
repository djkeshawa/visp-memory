#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

echo "Starting Visp Memory Server"
echo "Dashboard: http://localhost:8000/dashboard"
echo "API docs:  http://localhost:8000/docs"

exec "$SCRIPT_DIR/visp-memory" serve --port "${VISP_MEMORY_PORT:-8000}"
