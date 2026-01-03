# LLM Memory

**Human-inspired memory system for LLMs** - persistent context, compressed knowledge, and intent tracking.

---

## The Problem

Every time an LLM starts a session, it has to:
1. Read through files
2. Analyze the codebase
3. Figure out patterns and conventions
4. Understand past decisions
5. Form an understanding of what's going on

**This happens every single session**, even if nothing changed.

## The Solution

LLM Memory mimics how human developers remember projects:

- **Episodic Memory** - Events that happened (bugs fixed, decisions made)
- **Semantic Memory** - Knowledge extracted from experience (patterns, rules, warnings)
- **Intent Memory** - Current goals and direction (what we're working on)

Instead of re-analyzing everything, the LLM gets pre-formed understanding instantly.

---

## Quick Start

### Installation

```bash
pip install llm-memory

# Or with optional dependencies
pip install llm-memory[openai]  # For OpenAI embeddings
pip install llm-memory[ollama]  # For Ollama embeddings
```

### Initialize

```bash
cd your-project
llm-memory init --type code
```

### Basic Usage

```bash
# Record what happened
llm-memory record "Fixed authentication race condition" -c bug_fixed
llm-memory decision "Use JWT tokens" "Stateless scaling needed" --alt "Sessions" --alt "OAuth"

# Establish knowledge
llm-memory learn "Auth module requires mutex locks for token refresh" -c invariant
llm-memory warn "database/migrations" "Always backup before running"
llm-memory convention "All API endpoints return explicit error objects"

# Set direction
llm-memory goal "Implement OAuth2 authentication" -p 2
llm-memory working "Token refresh endpoint" --file auth/refresh.py
llm-memory done

# Get context for LLM
llm-memory context
llm-memory context --format json

# Search memories
llm-memory recall "authentication"
llm-memory relevant --task "fix login bug" --file auth/login.py
```

---

## Memory Layers

### Episodic (What Happened)

Events with context. Get compressed over time.

```bash
llm-memory record "Deployed v2.0 to production"
llm-memory decision "Chose PostgreSQL" "Need ACID + complex queries" --alt MongoDB
llm-memory bug "Token refresh race condition" --fix "Added mutex lock" --file auth/token.py
```

Categories: `architecture_decision`, `trade_off`, `bug_fixed`, `bug_found`, `feature_added`, `refactor`, `incident`, `discovery`, `note`

### Semantic (What We Know)

Timeless knowledge. Applies across contexts.

```bash
llm-memory learn "Connection pooling is tuned for current load" -c invariant
llm-memory warn "auth/" "Fragile - test thoroughly before changes"
llm-memory convention "Prefer explicit errors over silent failures"
llm-memory issue "Memory leak in image processing" --workaround "Restart every 1000 requests"
```

Categories: `invariant`, `behavior`, `contract`, `pattern`, `antipattern`, `best_practice`, `fragile_area`, `known_issue`, `gotcha`, `convention`

### Intent (Where We're Going)

Current direction. Guides decisions.

```bash
llm-memory goal "Ship v2.0 by Friday" -p 3  # critical priority
llm-memory focus "Bug fixes only" --avoid "New features" --avoid "Refactoring"
llm-memory working "OAuth implementation" --file auth/oauth.py
llm-memory done
```

---

## Python API

```python
from llm_memory import Memory

# Initialize (auto-discovers config)
memory = Memory()

# Or with explicit config
from llm_memory import MemoryConfig
memory = Memory(config=MemoryConfig(project_type="code"))

# Record events
memory.record("Fixed login bug", category="bug_fixed", importance=0.7)
memory.decision(
    what="Use Redis for caching",
    why="Need distributed cache for horizontal scaling",
    alternatives=["Memcached", "In-memory"]
)

# Establish knowledge
memory.learn("The auth service requires warm-up time after restart", category="behavior")
memory.warn("database/migrations", "Rollbacks are unreliable")

# Set intent
memory.goal("Implement user dashboard", priority=2, constraints=["No new dependencies"])
memory.working_on("Dashboard API endpoints", files=["api/dashboard.py"])
memory.done()

# Search
results = memory.recall("authentication")
relevant = memory.relevant_for(task="fix caching", files=["cache/redis.py"])

# Get context (for LLM injection)
context_text = memory.context()  # Human-readable
context_json = memory.context(format="json")  # Structured

# Access layers directly
memory.episodic.recent(limit=10)
memory.semantic.get_warnings()
memory.intent.get_active()
```

---

## Configuration

### llm-memory.yaml

```yaml
project_name: my-project
project_type: code  # code, writing, research, general

embedding:
  provider: sentence-transformers  # or openai, ollama
  model: all-MiniLM-L6-v2

storage:
  data_dir: .llm-memory/data
  vector_db: chroma

compression:
  episodic_threshold: 10  # Episodes before compression
  compression_level: 0.5
  llm_provider: openai  # Optional: use LLM for smart compression
  llm_model: gpt-4o-mini

auto_compress: true
decay_enabled: true
decay_halflife_days: 30
```

### Environment Variables

```bash
LLM_MEMORY_EMBEDDING_PROVIDER=openai
LLM_MEMORY_EMBEDDING_API_KEY=sk-...
LLM_MEMORY_COMPRESSION_LLM_PROVIDER=openai
```

---

## Memory Compression

Over time, episodic memories compress into semantic knowledge:

```
Episodic:
- "Jan 3: Fixed auth bug by adding mutex"
- "Jan 5: Another auth race condition, added lock"
- "Jan 8: Auth deadlock, refactored locking"

↓ Compression ↓

Semantic:
- "Auth module is prone to race conditions. Always use mutex locks."
```

Run manually:
```bash
llm-memory compress
```

Or enable auto-compression in config.

---

## Memory Decay

Unused memories fade over time (like human memory):

```bash
llm-memory decay
```

Memories accessed frequently stay important. Forgotten ones fade to minimum importance.

---

## Context Output

The `context` command generates LLM-ready output:

```markdown
# Project Memory Context

## Current Direction
**Focus:** Bug fixes only
**Working on:** Token refresh endpoint
**Constraints:**
- AVOID: New features
- AVOID: Refactoring

## Warnings
- WARNING [auth/]: Fragile - test thoroughly before changes
- WARNING [database/migrations]: Always backup before running

## Conventions
- All API endpoints return explicit error objects
- Prefer explicit errors over silent failures

## Known Issues
- Memory leak in image processing. Workaround: Restart every 1000 requests

## Recent Activity
- [bug_fixed] Fixed authentication race condition
- [decision] Decision: Use JWT tokens
```

---

## Integration with LLMs

### Claude Code / Cursor / Aider

Add to your system prompt or CLAUDE.md:

```markdown
## Project Memory

Before starting work, load context:
\`\`\`bash
llm-memory context
\`\`\`

After making decisions, record them:
\`\`\`bash
llm-memory decision "what" "why"
\`\`\`

After fixing bugs:
\`\`\`bash
llm-memory bug "description" --fix "solution"
\`\`\`
```

### Programmatic Integration

```python
from llm_memory import Memory

memory = Memory()

# Get context to inject into prompt
context = memory.context(format="json")

# Build your prompt
prompt = f"""
{context}

User request: {user_message}
"""

# After LLM responds, record what happened
memory.record("Implemented feature X")
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        LLM Memory                           │
├─────────────────────────────────────────────────────────────┤
│  Intent Layer          │  "What are we doing?"              │
│  (goals, focus,        │  Short-term, high influence        │
│   constraints)         │                                    │
├────────────────────────┼────────────────────────────────────┤
│  Semantic Layer        │  "What do we know?"                │
│  (knowledge, patterns, │  Timeless, applies everywhere      │
│   warnings, rules)     │                                    │
├────────────────────────┼────────────────────────────────────┤
│  Episodic Layer        │  "What happened?"                  │
│  (events, decisions,   │  Time-bound, compresses over time  │
│   bugs, discoveries)   │                                    │
├─────────────────────────────────────────────────────────────┤
│  Storage: SQLite (structured) + ChromaDB (vectors)          │
│  Embeddings: sentence-transformers / OpenAI / Ollama        │
└─────────────────────────────────────────────────────────────┘
```

---

## Comparison

| Feature | LLM Memory | RAG | Vector DB |
|---------|------------|-----|-----------|
| Structured knowledge | ✅ | ❌ | ❌ |
| Intent/goal tracking | ✅ | ❌ | ❌ |
| Memory compression | ✅ | ❌ | ❌ |
| Memory decay | ✅ | ❌ | ❌ |
| Semantic search | ✅ | ✅ | ✅ |
| Human-like memory | ✅ | ❌ | ❌ |
| Time-aware | ✅ | ❌ | ❌ |

---

## License

MIT

---

## Contributing

PRs welcome! Areas of interest:
- MCP server integration
- More embedding providers
- Smarter compression algorithms
- IDE integrations
