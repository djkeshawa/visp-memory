# LLM Memory

**Human-inspired memory system for LLMs** - persistent context, compressed knowledge, and intent tracking.

> **Vision**: A central, cross-functional memory that evolves with your team. Starting as a local tool for individuals, it will grow into a shared organizational brain that tracks context across multiple repositories.

---

## The Problem

Every time an LLM starts a session, it has to re-learn your project from scratch: files, patterns, past decisions, and goals. This **"Context Amnesia"** leads to repetitive explanations and lost knowledge.

## The Solution

LLM Memory creates a persistent cognitive layer that mimics human memory:

1.  **Episodic Memory** ("What happened"): Events, bugs fixed, decisions made.
2.  **Semantic Memory** ("What we know"): Patterns, rules, and warnings extracted from experience.
3.  **Intent Memory** ("Where we're going"): Current goals and constraints.

By injecting this pre-formed context, your LLM (Claude, ChatGPT, etc.) instantly understands *why* the code is written this way and *what* you're trying to achieve.

---

## Quick Start (Local Mode)

Currently, LLM Memory runs in **Local Mode** (SQLite per project).

### 1. Install
```bash
pip install llm-memory
```

### 2. Initialize
```bash
cd your-project
llm-memory init --type code
```

### 3. Record & Recall
```bash
# Record a decision
llm-memory decision "Use JWT tokens" "Stateless scaling needed"

# Save a warning for future sessions
llm-memory warn "auth/token.py" "Race condition possible - use mutex"

# Get context for your LLM
llm-memory context | xclip -sel clip
```

---

## Project Status & Roadmap

We are actively evolving towards a **Central Memory System**.

- **Phase 1: Foundation (Current)** - Local SQLite storage, core memory layers, CLI/MCP interfaces. ✅
- **Phase 2: Active Memory** - Automatic capture from Git commits, test failures, and conversations. 🚧
- **Phase 3: Central Memory** - Shared server for teams and cross-repo context (Service A knowing about Service B's breaking changes). 🔮

See [ROADMAP.md](ROADMAP.md) for details.

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

## Comparisons

| Feature | LLM Memory | RAG | Vector DB |
|---------|------------|-----|-----------|
| **Structured Knowledge** | ✅ | ❌ | ❌ |
| **Goal Tracking** | ✅ | ❌ | ❌ |
| **Memory Compression** | ✅ | ❌ | ❌ |
| **Semantic Search** | ✅ | ✅ | ✅ |
| **Time-Awareness** | ✅ | ❌ | ❌ |

---

## License

MIT
