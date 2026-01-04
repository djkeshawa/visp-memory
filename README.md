# LLM Memory

**Human-inspired memory system for LLMs** - persistent context, compressed knowledge, and intent tracking.

> **Vision**: A central, cross-functional memory that evolves with your team. Starting as a local tool for individuals, it will grow into a shared organizational brain that tracks context across multiple repositories.

![LLM Memory Dashboard](https://raw.githubusercontent.com/yourusername/llm-memory/main/docs/dashboard-preview.png)

---

## 📚 Documentation & Resources

| File | Description |
|------|-------------|
| [**PACKAGING.md**](PACKAGING.md) | Detailed distribution guide (Docker, Standalone, Pip) |
| [**ROADMAP.md**](ROADMAP.md) | Project vision and future phases |
| [**BUGFIXES-2.md**](BUGFIXES-2.md) | Latest bug fixes (Jan 4, 2026) |
| [**BUGFIXES.md**](BUGFIXES.md) | Previous bug fixes (Jan 3, 2026) |

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
| **API** | REST API | Full programmatic access to memory graph |

---

## 📦 Installation

Choose the method that fits your workflow.

### Method 1: Python Package (Recommended)

Install via pip. This includes the CLI, API server, and embedded dashboard.

```bash
pip install llm-memory[all]
```

### Method 2: Docker

Run the full system in a container (ideal for servers/teams).

```bash
docker run -p 8000:8000 -v ~/.llm-memory:/data llm-memory:latest
```

### Method 3: Standalone Executable

Updates for non-Python users. Download the latest release for your platform (Linux/macOS/Windows).

1.  Download from [Releases](https://github.com/yourusername/llm-memory/releases)
2.  Extract the archive
3.  Run `./llm-memory`

For detailed build and distribution instructions, see [PACKAGING.md](PACKAGING.md).

---

## ⚡ Quick Start

### 1. Initialize

Initialize LLM Memory in your project root:

```bash
llm-memory init --type code
```

### 2. Start the Server & Dashboard

Launch the central server. The dashboard will be available at `http://localhost:8000/dashboard`.

```bash
llm-memory serve
```

### 3. Record & Recall

Start building your project's memory:

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
- `memory_decision`: Document architectural choices.
- `memory_warn`: Flag fragile code areas.
- `memory_goal`: Manage project intent.
- `memory_file_context`: Get proactive context for specific files.

---

## 🌐 Workspace Scope & Multi-Project Usage

LLM Memory is designed to **share knowledge across your entire workspace by default**, enabling cross-project learning.

### Default: Unified Workspace
All memories are stored in a shared graph. Memories created in Project A are accessible in Project B if relevant.

### Project Isolation
For completely separate contexts (e.g., client work), use the `--repo` flag or configuration:

```bash
# Initialize with specific scope
llm-memory init --repo client-xyz

# Or per-command
llm-memory record "Secret stuff" --repo secret-project
```

---

## 🛠️ Development

If you want to contribute or modify the dashboard:

```bash
# Clone repository
git clone https://github.com/yourusername/llm-memory.git
cd llm-memory

# Install in editable mode
make install-dev

# Build everything
make build

# Run tests
make test
```

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
        Neo4j[(Neo4j / ChromaDB)]
    end

    User --> CLI
    User --> MCP
    Dash --> API
    
    CLI --> Memory Core
    MCP --> Memory Core
    API --> Memory Core
    
    Memory Core --> Neo4j
```

---

## License

MIT
