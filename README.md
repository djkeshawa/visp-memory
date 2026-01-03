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

## 🚀 Capabilities

| Interface | Description | Key Features |
|-----------|-------------|--------------|
| **CLI** | Command Line Tool | `llm-memory record`, `recall`, `decision`, `warn` |
| **MCP Server** | Model Context Protocol | Exposes memory tools directly to Claude/IDE |
| **Dashboard** | Web Interface | Graph visualization, intent management, stats |

---

## 📦 Installation

```bash
pip install llm-memory
```

To install all dependencies (API, MCP, Capture):
```bash
pip install "llm-memory[all]"
```

---

## ⚡ Quick Start (CLI)

Initialize LLM Memory in your project root:

```bash
llm-memory init --type code
```

### Core Commands

```bash
# Record a decision
llm-memory decision "Use JWT tokens" "Stateless scaling needed"

# Save a warning for specific files
llm-memory warn "src/auth.py" "Race condition possible - use mutex"

# Set a goal
llm-memory goal "Refactor Database Layer" --priority 2

# Search memories
llm-memory recall "authentication"

# Get full context for your LLM (copy to clipboard)
llm-memory context | xclip -sel clip
```

---

## 🤖 MCP Server (Claude Desktop / IDEs)

LLM Memory implements the **Model Context Protocol (MCP)**, allowing AI assistants to directly read and write to your project's memory.

### Configuration

Add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "llm-memory": {
      "command": "llm-memory-mcp",
      "args": []
    }
  }
}
```

### Available Tools
- `memory_recall`: Search past events and knowledge.
- `memory_record`: Save new findings or events.
- `memory_decision`: document architectural choices.
- `memory_warn`: Flag fragile code areas.
- `memory_goal`: Manage project intent.
- `memory_file_context`: Get proactive context for specific files.

---

## 📊 Web Dashboard

Visualize your project's memory graph and manage intents visually.

### 1. Start the Backend API
```bash
# Run from your project root
python3 -m uvicorn llm_memory.server.app:app --port 8000
```
*Note: Ensure you have `pip install "llm-memory[api]"`*

### 2. Start the Frontend
The dashboard is located in `llm-memory-dashboard`.

```bash
cd llm-memory-dashboard
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000) (or the port shown in terminal) to view the graph.

---

## 🌐 Workspace Scope & Multi-Project Usage

LLM Memory is designed to **share knowledge across your entire workspace by default**, enabling cross-project learning and intelligent connections.

### Default Behavior: Unified Workspace Memory

By default, all memories are stored **without isolation**, creating a shared knowledge base across all your projects:

```bash
# These all share the same memory space
cd ~/project-a
llm-memory record "Implemented OAuth with JWT tokens"

cd ~/project-b  
llm-memory record "Frontend expects JWT in headers"

# The LLM can now connect these across projects!
llm-memory recall "authentication"
# Returns both memories, showing the relationship
```

**Why this matters:**
- 🔗 **Cross-project insights**: "I changed the auth system in service-a, which affects service-b"
- 📚 **Pattern reuse**: "This approach I used in project-x solved a similar problem in project-y"
- 🧠 **Unified knowledge**: Your LLM builds one coherent understanding of your entire codebase ecosystem

### Optional: Project Isolation

For **completely unrelated work** (client projects, experiments, etc.), use the `--repo` flag to create an isolated memory space:

```bash
# Isolated client work
llm-memory record "Client XYZ specific logic" --repo client-xyz

# Isolated experiment
llm-memory record "Testing new framework" --repo experiment-temp

# These won't appear in your main workspace recalls
llm-memory recall "framework"  # Won't show experiment-temp memories
```

### Configuration

#### Option 1: Per-Command Isolation
```bash
llm-memory record "..." --repo isolated-project
llm-memory recall "..." --repo isolated-project
```

#### Option 2: Set Default repo_id in Config
Edit `~/.llm-memory/config.yaml`:
```yaml
repo_id: client-xyz  # All commands now scoped to this project
```

Or use environment variable:
```bash
export LLM_MEMORY_REPO_ID=client-xyz
```

### Best Practices

✅ **DO**: Use shared workspace memory for:
- Related microservices
- Frontend + Backend of same product
- Internal tools and libraries
- Any projects that should learn from each other

❌ **DON'T**: Use isolation unless absolutely necessary:
- Client confidential work
- Throwaway experiments
- Completely unrelated domains

### Dashboard & API

The web dashboard shows your **unified workspace** by default. All memories without a `repo_id` are visible, creating the full knowledge graph.

To filter by project in API calls:
```bash
curl "http://localhost:8000/memories?repo_id=client-xyz"
```

---

---

## How It Works

### The Write Path (Capture & Learning)
1.  **Capture**: You record an event via CLI (`llm-memory record`), or the system auto-captures a Git commit.
2.  **Layering**: The event enters **Episodic Memory**.
3.  **Synthesis**: Over time, repeated episodes are compressed into **Semantic Memory** (patterns/rules).
4.  **Storage**: Metadata, relationships, and vector embeddings are stored in **Neo4j** (GraphRAG).

### The Read Path (Context Injection)
1.  **Trigger**: You ask for context via CLI (`llm-memory context`) or an MCP-enabled IDE requests it.
2.  **Recall**: The system fetches:
    *   Active **Intents** (Goal: "Refactor API")
    *   Relevant **Knowledge** (Rule: "Always use strict typing")
    *   Recent **Episodes** (Event: "Fixed auth bug yesterday")
    *   **Graph Traversals**: Related concepts via knowledge graph links.
3.  **Synthesis**: Data is formatted into a concise Markdown prompt.
4.  **Injection**: The prompt is fed to the LLM, giving it instant "memory" of the project.

---

## 🧠 Architecture

```mermaid
graph TD
    User[User / IDE]
    
    subgraph Interfaces
        CLI[CLI Tool]
        MCP[MCP Server]
        API[FastAPI Server]
        Dash[Web Dashboard]
    end

    subgraph Memory Core
        Intent[Intent Layer]
        Semantic[Semantic Layer]
        Episodic[Episodic Layer]
    end

    subgraph Storage
        Neo4j[(Neo4j)]
    end

    User --> CLI
    User --> MCP
    Dash --> API
    
    CLI --> Memory Core
    MCP --> Memory Core
    API --> Memory Core
    
    Memory Core --> Neo4j
```

## Comparisons

| Feature | LLM Memory | RAG | Vector DB |
|---------|------------|-----|-----------|
| **Structured Knowledge** | ✅ | ❌ | ❌ |
| **Goal Tracking** | ✅ | ❌ | ❌ |
| **Memory Graph** | ✅ | ❌ | ❌ |
| **Proactive Warnings** | ✅ | ❌ | ❌ |
| **Time-Awareness** | ✅ | ❌ | ❌ |

---

## License

MIT
