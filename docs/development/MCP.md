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
        "LLM_MEMORY_EMBEDDING_PROVIDER": "auto"
      }
    }
  }
}
```

For best recall, run the MCP server against an API server configured with
OpenAI or Ollama embeddings. In client mode, the Codex installer delegates
embedding generation to the server instead of doing local MCP-side embedding
work.

## Codex Configuration

Use the CLI installer when Codex should connect to a running llm-memory server:

```bash
llm-memory hooks install codex \
  --server-url http://127.0.0.1:8000 \
  --repo-id my-project
```

The installer adds a managed `mcp_servers.llm-memory` block to the Codex config
and writes project-level `AGENTS.md` instructions. Restart Codex after
installing so it loads the MCP server.

## Available Tools

Stable local workflow tools:

- `memory_prepare_task`: prepare the cited, token-budgeted memory brief for a task.
- `memory_context`: get full memory context for the current project.
- `memory_recall`: search past decisions, events, and knowledge.
- `memory_record`: store an event or finding.
- `memory_decision`: document an architecture/design decision.
- `memory_learn`: store reusable knowledge.
- `memory_warn`: flag a fragile file/module/area.
- `memory_goal`: set a goal or intent.
- `memory_file_context`: get context for a specific file.
- `memory_session_start`: start a Codex-style session with current context.
- `memory_before_change`: recall warnings and relevant history before edits.
- `memory_after_work`: record useful end-of-work summary, decisions, bugs, and warnings.
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

Prepare before planning or editing:

```json
{
  "task": "Fix session expiration in the dashboard",
  "files": ["src/llm_memory/server/auth_store.py"],
  "symbols": ["authenticate_session"],
  "token_budget": 1200,
  "format": "text"
}
```

`memory_prepare_task` is deterministic by default and returns evidence citations,
active intent, constraints, contradictions, and explicit unknowns. Reuse its
fingerprint to avoid reinjecting an unchanged brief.

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
