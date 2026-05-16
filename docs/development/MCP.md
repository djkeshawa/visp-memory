# MCP Integration

LLM Memory includes an MCP server so compatible AI assistants can read and write
project memory directly.

## Install

For local development from the checkout:

```bash
pip install -e ".[mcp]"
```

For a normal install:

```bash
pip install "llm-memory[mcp]"
```

## Claude Desktop Configuration

Use the installed console script:

```json
{
  "mcpServers": {
    "llm-memory": {
      "command": "llm-memory-mcp",
      "args": [],
      "cwd": "/path/to/your/project",
      "env": {
        "LLM_MEMORY_EMBEDDING_PROVIDER": "noop"
      }
    }
  }
}
```

For real semantic search, remove the `noop` setting and configure a supported
embedding provider.

## Available Tools

Stable local workflow tools:

- `memory_context`: get full memory context for the current project.
- `memory_recall`: search past decisions, events, and knowledge.
- `memory_record`: store an event or finding.
- `memory_decision`: document an architecture/design decision.
- `memory_learn`: store reusable knowledge.
- `memory_warn`: flag a fragile file/module/area.
- `memory_goal`: set a goal or intent.
- `memory_file_context`: get context for a specific file.
- `memory_stats`: show memory counts.

## Example Tool Calls

Record a decision:

```json
{
  "what": "Use SQLite as the local default",
  "why": "New users should not need Neo4j to start",
  "alternatives": ["Require Neo4j for all users"]
}
```

Recall relevant memory:

```json
{
  "query": "dashboard routing",
  "limit": 5
}
```

Warn about a file:

```json
{
  "area": "src/llm_memory/server/app.py",
  "warning": "Dashboard routes must stay under /dashboard to avoid API collisions",
  "severity": 0.8
}
```

## Verification

From this repository:

```bash
pytest tests/interfaces/test_mcp.py -q
python3 -c "from llm_memory.interfaces.mcp import MCP_AVAILABLE; print(MCP_AVAILABLE)"
```

`MCP_AVAILABLE` must be `True` in environments where the MCP server is expected
to run.
