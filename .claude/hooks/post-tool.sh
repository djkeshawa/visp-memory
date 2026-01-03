#!/bin/bash
# Post-tool hook - records file changes to llm-memory
set -e

cd "$CLAUDE_PROJECT_DIR"

# Check if llm-memory is available
if ! command -v llm-memory &> /dev/null; then
    exit 0
fi

# Read the tool input from stdin
tool_input=$(cat)

# Extract tool name and file path
tool_name=$(echo "$tool_input" | jq -r '.tool_name // ""')
file_path=$(echo "$tool_input" | jq -r '.tool_input.file_path // ""')

# Only record if we have valid data
if [ -n "$file_path" ] && [ -n "$tool_name" ]; then
    # Get relative path for cleaner recording
    rel_path="${file_path#$CLAUDE_PROJECT_DIR/}"

    # Record the file modification silently
    llm-memory record "Modified: $rel_path" -c feature_added 2>/dev/null || true
fi

exit 0
