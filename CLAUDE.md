# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Memory System

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
```

Categories: `bug_fixed`, `feature_added`, `refactor`, `architecture_decision`, `discovery`

## Project Overview

LLM Memory is a human-inspired memory system for LLMs that provides persistent context across sessions. It implements three cognitive memory layers:

- **Episodic**: Time-bound events (bugs fixed, decisions made) that compress over time
- **Semantic**: Timeless knowledge (patterns, conventions, warnings)
- **Intent**: Current goals, focus, and constraints

## Development Commands

```bash
# Install in development mode
pip install -e ".[dev]"

# Run tests
pytest

# Run a single test
pytest tests/test_memory.py::TestMemory::test_basic_record

# Lint with ruff
ruff check src/
ruff format src/

# Run CLI
llm-memory --help
```

## Architecture

The system uses a layered architecture:

```
src/llm_memory/
├── core/
│   ├── memory.py     # Main Memory class - unified interface
│   ├── storage.py    # SQLite + ChromaDB storage abstraction
│   ├── compression.py # Memory consolidation (episodic → semantic)
│   └── embeddings.py # Embedding providers (sentence-transformers/OpenAI/Ollama)
├── layers/
│   ├── episodic.py   # Event-based memories
│   ├── semantic.py   # Knowledge/facts layer
│   └── intent.py     # Goals and direction
└── interfaces/
    ├── cli.py        # Typer CLI (llm-memory command)
    └── mcp.py        # MCP server for LLM tool integration
```

**Key patterns:**
- `Memory` class in `core/memory.py` is the main entry point, composing all three layers
- Each layer (`EpisodicMemory`, `SemanticMemory`, `IntentMemory`) operates on a shared `Storage` instance
- Storage uses SQLite for structured data and ChromaDB for vector embeddings
- Configuration uses pydantic-settings with env var support (`LLM_MEMORY_*` prefix)

## Configuration

Config is auto-discovered from `llm-memory.yaml`, `llm-memory.json`, or `.llm-memory/config.yaml` walking up from cwd. Environment variables override file config.

Key env vars:
- `LLM_MEMORY_EMBEDDING_PROVIDER`: sentence-transformers, openai, ollama
- `LLM_MEMORY_EMBEDDING_API_KEY`: API key for OpenAI embeddings
- `LLM_MEMORY_COMPRESSION_LLM_PROVIDER`: openai, ollama, anthropic (for smart compression)

## MCP Server

The MCP server exposes memory operations as tools for LLM integration.

```bash
# Run the MCP server
llm-memory serve

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
