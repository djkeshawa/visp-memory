#!/usr/bin/env bash
# End-to-end demo: mine a real repository, then show what memory would and would not say.
#
# Run against any git repository with some history:
#
#     ./scripts/demo.sh                  # uses the current directory
#     ./scripts/demo.sh ~/code/myproject
#
# Everything runs locally. No network, no API key.

set -euo pipefail

TARGET="${1:-$(pwd)}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

if ! command -v visp-memory >/dev/null 2>&1; then
    echo "visp-memory is not on PATH. Install with: pip install -e '.[mcp,capture]'" >&2
    exit 1
fi

if [ ! -d "$TARGET/.git" ]; then
    echo "Not a git repository: $TARGET" >&2
    exit 1
fi

echo "==> Cloning $TARGET into a scratch directory (your repo is not modified)"
git clone --quiet "$TARGET" "$WORK/repo"
cd "$WORK/repo"

echo
echo "==> 1. Mining existing history into memory"
echo "    (this is what a new user sees in their first minute)"
echo
visp-memory init --type code --force

echo
echo "==> 2. What the assistant would be told about a specific file"
echo
# `git ls-files | head -1` would kill the script under `set -o pipefail`: head exits
# after one line, git gets SIGPIPE, and the pipeline reports 141.
ALL_SOURCES="$(git ls-files '*.py' '*.ts' '*.go' '*.rs' '*.java' 2>/dev/null || true)"
FILE="${ALL_SOURCES%%$'\n'*}"
if [ -n "$FILE" ]; then
    visp-memory preview "review the error handling" --file "$FILE" || true
else
    visp-memory preview "review the error handling" || true
fi

echo
echo "==> 3. What it says when the task is too vague to answer"
echo "    (abstaining is the point: a confident wrong memory costs more than silence)"
echo
visp-memory preview "fix it" || true

echo
echo "==> 4. What it says about something the project knows nothing about"
echo
visp-memory preview "adjust the marketing landing page hero gradient" || true

echo
echo "==> 5. Where every memory came from, and whether it can be auto-injected"
echo
visp-memory audit --limit 8 || true

echo
echo "Done. Scratch directory removed; $TARGET was never modified."
