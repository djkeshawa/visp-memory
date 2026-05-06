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

## Phase 3: Central Memory (Multi-Repo) ✅ Complete

The "Killer Feature": A shared server that connects all repositories and team members.

### 3.1 Shared Server Architecture ✅
- ✅ **Mode Switch**: Support `local` (current) and `client` (server) modes
- ✅ **FastAPI Backend**: Modular routers for memories, intents, repos, teams
- ✅ **Auth**: JWT tokens and API key authentication

### 3.2 Multi-Repo Support ✅
- ✅ **Repository Context**: Repositories as first-class entities (`core/repository.py`)
- ✅ **Dependency Tracking**: Map relationships between repos (e.g., Service A depends on Lib B)
- ✅ **Cross-Repo Context**: `CrossRepoContext` aggregates warnings/knowledge from dependencies

### 3.3 Team Collaboration ✅
- ✅ **User & Team Models**: `core/team.py` with full CRUD
- ✅ **Teams Router**: API endpoints for user/team management
- ✅ **Ownership**: Attribute memories and repos to teams

### 3.4 Integration Updates ✅
- ✅ **JWT in SDK**: `RemoteStorage` supports Bearer token auth
- ✅ **Config Extension**: `jwt_token` field in `StorageConfig`
- ✅ **End-to-End Verification**: All integration tests passing

## Phase 4: Advanced Intelligence

- **Pattern Detection**: "This bug happens every time we touch the auth module."
- **Feedback Loops**: Learn which memories are useful based on user feedback.
- **Multi-Agent Sync**: Protocol for autonomous agents to share and sync context.
