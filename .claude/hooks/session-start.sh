#!/bin/bash
# Session start hook - loads llm-memory context for Claude Code
set -e

cd "$CLAUDE_PROJECT_DIR"

# Check if llm-memory is available
if ! command -v llm-memory &> /dev/null; then
    echo "llm-memory not found, skipping context load" >&2
    exit 0
fi

# Get memory context and output as additional context
context=$(llm-memory context --format text 2>/dev/null || echo "")

if [ -n "$context" ]; then
    # Output JSON for Claude to receive as context
    cat << EOF
{
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": "LLM Memory context loaded. Review the project memory for current goals, warnings, and recent activity."
  }
}
EOF
fi

exit 0
