# LLM Memory Roadmap

**Vision:** A central, human-inspired memory system for software teams. It starts as a local tool for individuals and evolves into a shared organizational memory that tracks context across multiple repositories.

---

## Phase 1: Foundation (Current Status)

The current version works as a **single-user, local** tool. It provides the core memory architecture but stores data in a local SQLite database per project.

- ✅ **Core Layers**: Episodic, Semantic, Intent
- ✅ **Storage**: SQLite + VectorDB (Chroma)
- ✅ **Interfaces**: CLI, Python API, MCP Server
- ✅ **Basic Features**: Recording, Recall, Context Generation

## Phase 2: Active Memory (Next Up)

Transform the system from passive note-taking to active participation.

### 2.1 Automatic Capture
- **Git Integration**: Auto-record commits, detect breaking changes, and infer intent from diffs.
- **Test Integration**: Record test failures as bugs; detect flaky tests.
- **Conversation Capture**: Extract learnings and decisions from LLM conversations.

### 2.2 Proactive Recall
- **File Triggers**: Surface warnings, bugs, and patterns when files are opened.
- **Error Matching**: Find similar past errors when exceptions occur.
- **IDE Integrations**: Adapters for Claude Code, Cursor, and VS Code.

### 2.3 Quality Management
- **Deduplication**: Detect and merge duplicate memories.
- **Conflict Resolution**: Identify contradictory information.
- **Smart Compression**: Hierarchical summarization (events → patterns → principles).

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
