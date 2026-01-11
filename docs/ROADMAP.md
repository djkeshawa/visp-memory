# LLM Memory Roadmap

**Vision:** A central, human-inspired memory system for software teams. It starts as a local tool for individuals and evolves into a shared organizational memory that tracks context across multiple repositories.

---

## Phase 1: Foundation ✅ Complete

The core memory system with all interfaces.

- ✅ **Core Layers**: Episodic, Semantic, Intent
- ✅ **Storage**: Neo4j (primary), SQLite + ChromaDB (local)
- ✅ **Interfaces**: CLI, Python API, MCP Server, REST API, Web Dashboard
- ✅ **Basic Features**: Recording, Recall, Context Generation

## Phase 2: Active Memory ✅ Mostly Complete

Active participation and automatic capture.

### 2.1 Automatic Capture ✅
- ✅ **Git Integration**: Auto-record commits, parse diffs, install hooks (`capture/git.py`)
- ✅ **Test Integration**: Capture test activity (`capture/tests.py`)
- ⏳ **Conversation Capture**: Extract learnings from LLM conversations (partial)

### 2.2 Proactive Recall ✅
- ✅ **File Triggers**: Surface warnings/bugs when files are opened (`recall/proactive.py`)
- ✅ **Error Matching**: Find similar past errors (`ProactiveRecall.on_error`)
- ✅ **IDE Integrations**: Claude Code, Cursor, Aider adapters (`hooks/`)

### 2.3 Quality Management ✅
- ✅ **Deduplication**: Detect and merge duplicates (`quality/dedup.py`)
- ⏳ **Conflict Resolution**: Identify contradictory information (partial)
- ✅ **Smart Compression**: Hierarchical summarization (`core/compression.py`)

## Phase 3: Central Memory (Multi-Repo)

The "Killer Feature": A shared server that connects all repositories and team members.

### 3.1 Shared Server Architecture
- **Mode Switch**: Support `local` (current) and `shared` (server) modes.
- **API**: HTTP API for remote memory access.
- **Auth**: Simple token-based authentication for teams.

### 3.2 Multi-Repo Support
- **Repository Context**: Treat repositories as first-class entities.
- **Dependency Tracking**: Map relationships between repos (e.g., Service A depends on Lib B).
- **Cross-Repo Context**: When working in Service A, see breaking changes and warnings from Lib B by default.

### 3.3 Team Collaboration
- **Shared Context**: Team members share the same memory stream.
- **Ownership**: Attribute memories to teams and authors.
- **Impact Analysis**: "Changing this API in Service A will break Service B and Mobile App."

## Phase 4: Advanced Intelligence

- **Pattern Detection**: "This bug happens every time we touch the auth module."
- **Feedback Loops**: Learn which memories are useful based on user feedback.
- **Multi-Agent Sync**: Protocol for autonomous agents to share and sync context.
