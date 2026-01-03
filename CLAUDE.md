# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Memory System

**First time setup** (if not already initialized):
```bash
llm-memory init  # Creates .llm-memory/ directory
```

**At the start of each session**, run:
```bash
llm-memory context
```

**After making significant changes**, record them:
```bash
llm-memory record "description" -c <category>
llm-memory decision "what" "why" --alt "alternative"
llm-memory bug "issue" --fix "solution"
```

**When learning something important about the codebase:**
```bash
llm-memory learn "knowledge" -c <category>
llm-memory warn "area" "warning"
llm-memory convention "practice" "rationale"
llm-memory issue "known problem"
```

Categories: `bug_fixed`, `feature_added`, `refactor`, `architecture_decision`, `discovery`

**TIP:** After installing hooks (`llm-memory hooks install claude-code`), context is automatically loaded at session start!

## Project Overview

LLM Memory is a human-inspired memory system for LLMs that provides persistent context across sessions. It implements three cognitive memory layers:

- **Episodic**: Time-bound events (bugs fixed, decisions made) that compress over time
- **Semantic**: Timeless knowledge (patterns, conventions, warnings)
- **Intent**: Current goals, focus, and constraints

## Development Commands

```bash
# Install in development mode
pip install -e ".[dev]"

# Install with all optional features
pip install -e ".[all]"  # Includes openai, ollama, api, mcp, capture, analysis

# Run tests
pytest

# Run a single test
pytest tests/test_memory.py::TestMemory::test_basic_record

# Lint with ruff
ruff check src/
ruff format src/

# Run CLI
llm-memory --help

# Run web UI (Next.js)
cd web && npm install && npm run dev
```

**Optional dependencies:**
- `[openai]` - OpenAI embeddings and LLM compression
- `[ollama]` - Ollama embeddings and LLM compression
- `[api]` - FastAPI server for shared memory
- `[mcp]` - MCP server for Claude Desktop integration
- `[capture]` - Git and test capture automation
- `[analysis]` - Deduplication and quality tools
- `[dev]` - Testing and linting tools

## Architecture

The system uses a layered architecture with automatic capture, proactive recall, and optional shared server:

```
src/llm_memory/
├── core/
│   ├── memory.py          # Main Memory class - unified interface
│   ├── storage.py         # Local SQLite + ChromaDB storage
│   ├── remote_storage.py  # Remote HTTP storage client
│   ├── compression.py     # Memory consolidation (episodic → semantic)
│   └── embeddings.py      # Embedding providers (sentence-transformers/OpenAI/Ollama)
├── layers/
│   ├── base.py            # Base memory layer abstraction
│   ├── episodic.py        # Event-based memories
│   ├── semantic.py        # Knowledge/facts layer
│   └── intent.py          # Goals and direction
├── capture/
│   ├── git.py             # Automatic git commit capture
│   └── tests.py           # Test failure capture (JUnit XML)
├── recall/
│   └── proactive.py       # Proactive memory surfacing
├── quality/
│   └── dedup.py           # Memory deduplication
├── hooks/
│   ├── base.py            # Base adapter for LLM tools
│   ├── claude_code.py     # Claude Code integration
│   ├── cursor.py          # Cursor integration
│   ├── aider.py           # Aider integration
│   └── generic.py         # Generic CLAUDE.md injection
├── server/
│   ├── app.py             # FastAPI server for shared memory
│   └── schemas.py         # API schemas
├── interfaces/
│   ├── cli.py             # Typer CLI (llm-memory command)
│   └── mcp.py             # MCP server for LLM tool integration
└── web/                   # Next.js web UI (optional)
```

**Key patterns:**
- `Memory` class in `core/memory.py` is the main entry point, composing all three layers
- Each layer (`EpisodicMemory`, `SemanticMemory`, `IntentMemory`) extends `BaseMemoryLayer` and operates on a shared `Storage` instance
- Storage can be local (`LocalStorage`) or remote (`RemoteStorage`) - configured via `storage.mode`
- Hooks enable automatic context injection for different LLM tools (Claude Code, Cursor, Aider)
- Configuration uses pydantic-settings with env var support (`LLM_MEMORY_*` prefix)

## Configuration

Config is auto-discovered from `llm-memory.yaml`, `llm-memory.json`, or `.llm-memory/config.yaml` walking up from cwd. Environment variables override file config.

Key env vars:
- `LLM_MEMORY_EMBEDDING_PROVIDER`: sentence-transformers, openai, ollama
- `LLM_MEMORY_EMBEDDING_API_KEY`: API key for OpenAI embeddings
- `LLM_MEMORY_COMPRESSION_LLM_PROVIDER`: openai, ollama, anthropic (for smart compression)
- `LLM_MEMORY_STORAGE_MODE`: local (default) or remote
- `LLM_MEMORY_STORAGE_REMOTE_URL`: URL of remote memory server (if mode=remote)

## Automatic Capture

The system can automatically capture memories from development activity:

```bash
# Capture from git commits
llm-memory capture git commit       # Capture last commit
llm-memory capture git range main.. # Capture range of commits
llm-memory capture git auto         # Auto-capture on each commit (git hook)

# Capture from test failures
llm-memory capture tests pytest-report.xml  # From JUnit XML
```

**Git capture** extracts:
- What changed (files, functions)
- Intent from commit message
- Breaking changes detection
- Bug fixes (via "fix" keywords)

**Test capture** records:
- Test failures as bugs
- Error messages and stack traces
- Flaky test detection

## LLM Tool Hooks

Integrate memory into your LLM coding assistant workflow:

```bash
# List available integrations
llm-memory hooks list

# Install hooks for a specific tool
llm-memory hooks install claude-code  # Creates .claude/startup.sh
llm-memory hooks install cursor       # Updates .cursorrules
llm-memory hooks install aider        # Creates .aider.conf.yml

# Update context (refresh CLAUDE.md with latest memory)
llm-memory hooks update claude-code

# Remove integration
llm-memory hooks uninstall claude-code
```

**Supported tools:**
- **Claude Code**: Injects context via startup hook (`.claude/startup.sh`)
- **Cursor**: Updates `.cursorrules` file
- **Aider**: Updates `.aider.conf.yml`
- **Generic**: Updates `CLAUDE.md` for any tool that reads it

When installed, the hook automatically loads relevant context at session start, showing:
- Active goals and current work
- Warnings for files you're touching
- Recent decisions and bug fixes
- Project conventions

## Memory Quality

```bash
# Find and merge duplicate memories
llm-memory dedup

# Compress old episodic memories → semantic knowledge
llm-memory compress

# Decay importance of old, unused memories
llm-memory decay
```

## Shared Memory Server (Multi-Repo)

Run a central memory server for team collaboration and cross-repo context:

```bash
# Start the server (requires: pip install llm-memory[api])
llm-memory serve --host 0.0.0.0 --port 8000

# Configure client to use remote storage
# In llm-memory.yaml:
# storage:
#   mode: remote
#   remote_url: http://localhost:8000
```

The server exposes a REST API for:
- Recording memories across repositories
- Searching and recall
- Cross-repo dependency tracking (planned)
- Team collaboration (planned)

**Note**: Server is currently a skeleton implementation. See `ROADMAP.md` for Phase 3 details.

## MCP Server

The MCP server exposes memory operations as tools for LLM integration with Claude Desktop and other MCP clients.

```bash
# Run the MCP server (stdio mode for Claude Desktop)
llm-memory-mcp

# Or directly
python -m llm_memory.interfaces.mcp
```

**Claude Desktop config** (`claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "llm-memory": {
      "command": "llm-memory-mcp",
      "cwd": "/path/to/your/project"
    }
  }
}
```

**Tools (17 total):**
- Context: `memory_context`, `memory_recall`, `memory_relevant`
- Recording: `memory_record`, `memory_decision`
- Knowledge: `memory_learn`, `memory_warn`, `memory_issue`
- Intent: `memory_goal`, `memory_working_on`, `memory_done`, `memory_clear_goals`
- Utility: `memory_stats`, `memory_list_warnings`, `memory_list_intents`
- Maintenance: `memory_compress`, `memory_decay`

**Resources**: `memory://context`, `memory://warnings`, `memory://goals`, `memory://conventions`, `memory://stats`

**Prompts**: `start_session`, `before_change`, `end_session`

## Web UI (Optional)

A Next.js web interface provides visual memory management:

```bash
cd web
npm install
npm run dev  # Starts on http://localhost:3000
```

Features:
- Browse memories by layer (episodic, semantic, intent)
- Search and filter
- Timeline view for episodic memories
- Deduplication interface
- Statistics dashboard

**Note**: Web UI connects to the memory server (`llm-memory serve`) or local storage.

## Additional CLI Commands

```bash
# Export/Import
llm-memory export > backup.json
llm-memory import backup.json

# List memories
llm-memory list                    # Recent memories
llm-memory list-intents            # Active goals
llm-memory list-warnings           # All warnings
llm-memory list-issues             # Known issues

# Proactive features
llm-memory inject file1.py file2.py  # Get context for specific files
llm-memory find-error "TypeError: ..." # Find similar past errors

# System
llm-memory stats                   # Memory statistics
llm-memory status                  # System status dashboard
```
