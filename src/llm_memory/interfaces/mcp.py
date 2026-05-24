"""
MCP (Model Context Protocol) Server for LLM Memory.

Exposes the memory system as MCP tools that LLMs can call directly.

Usage:
    # Run as standalone server
    python -m llm_memory.interfaces.mcp

    # Or use the CLI
    llm-memory serve

Configuration for Claude Desktop (claude_desktop_config.json):
    {
      "mcpServers": {
        "llm-memory": {
          "command": "python",
          "args": ["-m", "llm_memory.interfaces.mcp"],
          "cwd": "/path/to/your/project"
        }
      }
    }

Or with uvx:
    {
      "mcpServers": {
        "llm-memory": {
          "command": "uvx",
          "args": ["llm-memory", "serve"],
          "cwd": "/path/to/your/project"
        }
      }
    }
"""

import json
import logging
from datetime import datetime
from typing import Any

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import (
        AnyUrl,
        GetPromptResult,
        Prompt,
        PromptArgument,
        PromptMessage,
        Resource,
        TextContent,
        Tool,
    )

    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False

from llm_memory import Memory

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("llm-memory-mcp")


def create_mcp_server() -> "Server":
    """Create and configure the MCP server."""
    if not MCP_AVAILABLE:
        raise ImportError("MCP package not installed. Install with: pip install llm-memory[mcp]")

    server = Server("llm-memory")
    memory = Memory()

    # =========================================================================
    # Tool Definitions
    # =========================================================================

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        """List all available memory tools."""
        return [
            # Context & Search
            Tool(
                name="memory_context",
                description=(
                    "Get full project memory context including goals, warnings, and conventions."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "format": {
                            "type": "string",
                            "enum": ["text", "json"],
                            "default": "text",
                            "description": "Output format",
                        },
                        "include_history": {
                            "type": "boolean",
                            "default": True,
                            "description": "Include recent events",
                        },
                    },
                },
            ),
            Tool(
                name="memory_recall",
                description="Search memories for past decisions, knowledge, and events.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "What to search for (natural language)",
                        },
                        "limit": {
                            "type": "integer",
                            "default": 10,
                            "description": "Maximum results to return",
                        },
                        "repo_id": {
                            "type": "string",
                            "description": (
                                "Filter by repository/project ID for isolation (optional)"
                            ),
                        },
                    },
                    "required": ["query"],
                },
            ),
            Tool(
                name="memory_remember",
                description="Recall the latest memory in the current repository scope.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "repo_id": {"type": "string", "description": "Repository/project ID"},
                        "layer": {
                            "type": "string",
                            "enum": ["raw", "episodic", "semantic", "intent"],
                            "description": "Optional memory layer filter",
                        },
                        "category": {"type": "string", "description": "Optional category filter"},
                    },
                },
            ),
            Tool(
                name="memory_relevant",
                description="Get memories relevant to specific files or a task.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "task": {"type": "string", "description": "Task description"},
                        "files": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Files being worked on",
                        },
                    },
                },
            ),
            # Proactive Recall
            Tool(
                name="memory_file_context",
                description=(
                    "Get proactive context for a specific file "
                    "(warnings, bugs, decisions, knowledge)."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string", "description": "Path to the file"},
                        "include_related": {
                            "type": "boolean",
                            "default": True,
                            "description": "Include related files",
                        },
                    },
                    "required": ["file_path"],
                },
            ),
            Tool(
                name="memory_find_error",
                description="Find similar past errors with fixes and workarounds.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "error_message": {
                            "type": "string",
                            "description": "The error message to search for",
                        },
                        "error_type": {
                            "type": "string",
                            "description": "Optional error type (e.g., TypeError, ValueError)",
                        },
                        "file_path": {
                            "type": "string",
                            "description": "Optional file where error occurred",
                        },
                        "limit": {
                            "type": "integer",
                            "default": 5,
                            "description": "Maximum similar errors to return",
                        },
                    },
                    "required": ["error_message"],
                },
            ),
            Tool(
                name="memory_directory_context",
                description="Get aggregated knowledge for an entire directory.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "dir_path": {"type": "string", "description": "Directory path"},
                        "recursive": {
                            "type": "boolean",
                            "default": False,
                            "description": "Include subdirectories",
                        },
                    },
                    "required": ["dir_path"],
                },
            ),
            # Codex Workflow
            Tool(
                name="memory_session_start",
                description=(
                    "Start a work session: set the current task and return project/task memory."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "task": {"type": "string", "description": "Task being started"},
                        "files": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Files likely to be modified",
                        },
                        "repo_id": {
                            "type": "string",
                            "description": "Repository/project ID (optional)",
                        },
                    },
                },
            ),
            Tool(
                name="memory_before_change",
                description=(
                    "Recall warnings, conventions, decisions, and past bugs before editing files."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "task": {"type": "string", "description": "Task description"},
                        "files": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Files about to be edited",
                        },
                    },
                },
            ),
            Tool(
                name="memory_after_work",
                description=(
                    "Record the useful end-of-work memory: summary, decisions, bugs, warnings."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "summary": {"type": "string", "description": "What changed or was learned"},
                        "category": {
                            "type": "string",
                            "enum": [
                                "note",
                                "bug_fixed",
                                "feature_added",
                                "refactor",
                                "discovery",
                                "architecture_decision",
                                "investigation",
                            ],
                            "default": "note",
                        },
                        "decisions": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Design or release decisions to preserve",
                        },
                        "bugs_fixed": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Bugs fixed or root causes discovered",
                        },
                        "warnings": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "area": {"type": "string"},
                                    "warning": {"type": "string"},
                                },
                                "required": ["area", "warning"],
                            },
                            "description": "Fragile areas future agents should know",
                        },
                        "repo_id": {
                            "type": "string",
                            "description": "Repository/project ID (optional)",
                        },
                    },
                    "required": ["summary"],
                },
            ),
            # Recording Events
            Tool(
                name="memory_record",
                description="Record an event (bug fix, change, discovery).",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "event": {"type": "string", "description": "What happened"},
                        "category": {
                            "type": "string",
                            "enum": [
                                "note",
                                "bug_fixed",
                                "bug_found",
                                "feature_added",
                                "refactor",
                                "discovery",
                                "incident",
                                "architecture_decision",
                                "trade_off",
                                "design_choice",
                                "investigation",
                                "experiment",
                            ],
                            "default": "note",
                            "description": "Type of event",
                        },
                        "importance": {
                            "type": "number",
                            "minimum": 0,
                            "maximum": 1,
                            "default": 0.5,
                            "description": "How important (0.0-1.0)",
                        },
                        "repo_id": {
                            "type": "string",
                            "description": "Repository/project ID for isolation (optional)",
                        },
                    },
                    "required": ["event"],
                },
            ),
            Tool(
                name="memory_decision",
                description="Record an architecture or design decision with reasoning.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "what": {"type": "string", "description": "What was decided"},
                        "why": {"type": "string", "description": "Why this choice was made"},
                        "alternatives": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Alternatives that were considered",
                        },
                        "repo_id": {
                            "type": "string",
                            "description": "Repository/project ID (optional)",
                        },
                    },
                    "required": ["what", "why"],
                },
            ),
            # Establishing Knowledge
            Tool(
                name="memory_learn",
                description="Establish semantic knowledge - a fact, pattern, or rule.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "knowledge": {
                            "type": "string",
                            "description": "The knowledge/fact/pattern",
                        },
                        "category": {
                            "type": "string",
                            "enum": [
                                "fact",
                                "invariant",
                                "behavior",
                                "pattern",
                                "convention",
                                "best_practice",
                                "contract",
                                "antipattern",
                                "gotcha",
                            ],
                            "default": "fact",
                            "description": "Type of knowledge",
                        },
                        "importance": {
                            "type": "number",
                            "minimum": 0,
                            "maximum": 1,
                            "default": 0.6,
                            "description": "How important (0.0-1.0)",
                        },
                        "repo_id": {
                            "type": "string",
                            "description": "Repository/project ID (optional)",
                        },
                        "detect_conflicts": {
                            "type": "boolean",
                            "default": False,
                            "description": "Check for contradictions with existing knowledge",
                        },
                    },
                    "required": ["knowledge"],
                },
            ),
            Tool(
                name="memory_warn",
                description="Add a warning about a fragile or dangerous area.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "area": {"type": "string", "description": "Area/file/module to warn about"},
                        "warning": {"type": "string", "description": "What to watch out for"},
                        "severity": {
                            "type": "number",
                            "minimum": 0,
                            "maximum": 1,
                            "default": 0.7,
                            "description": "How serious (0.0-1.0)",
                        },
                        "repo_id": {
                            "type": "string",
                            "description": "Repository/project ID (optional)",
                        },
                    },
                    "required": ["area", "warning"],
                },
            ),
            Tool(
                name="memory_issue",
                description="Document a known issue with optional workaround.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "issue": {"type": "string", "description": "Issue description"},
                        "workaround": {"type": "string", "description": "How to work around it"},
                        "priority": {
                            "type": "number",
                            "minimum": 0,
                            "maximum": 1,
                            "default": 0.5,
                            "description": "Priority to fix (0.0-1.0)",
                        },
                    },
                    "required": ["issue"],
                },
            ),
            # Intent Management
            Tool(
                name="memory_goal",
                description="Set a goal or intent with optional constraints.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "goal": {"type": "string", "description": "Goal description"},
                        "priority": {
                            "type": "integer",
                            "enum": [0, 1, 2, 3],
                            "default": 1,
                            "description": "Priority: 0=low, 1=normal, 2=high, 3=critical",
                        },
                        "constraints": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Constraints to respect",
                        },
                        "repo_id": {
                            "type": "string",
                            "description": "Repository/project ID (optional)",
                        },
                    },
                    "required": ["goal"],
                },
            ),
            Tool(
                name="memory_working_on",
                description="Set what is currently being worked on. Helps track context.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "task": {"type": "string", "description": "What you're working on"},
                        "files": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Files being modified",
                        },
                        "repo_id": {
                            "type": "string",
                            "description": "Repository/project ID (optional)",
                        },
                    },
                    "required": ["task"],
                },
            ),
            Tool(
                name="memory_done",
                description="Clear current task (mark as done).",
                inputSchema={"type": "object", "properties": {}},
            ),
            Tool(
                name="memory_update_intent",
                description="Update an existing goal or intent by ID.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "intent_id": {"type": "string", "description": "Intent ID to update"},
                        "description": {"type": "string", "description": "Updated description"},
                        "priority": {
                            "type": "integer",
                            "enum": [0, 1, 2, 3],
                            "description": "Priority: 0=low, 1=normal, 2=high, 3=critical",
                        },
                        "status": {
                            "type": "string",
                            "enum": ["active", "completed", "closed"],
                            "description": "Updated intent status",
                        },
                    },
                    "required": ["intent_id"],
                },
            ),
            Tool(
                name="memory_close_intent",
                description="Close an intent by ID without marking it completed.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "intent_id": {"type": "string", "description": "Intent ID to close"},
                    },
                    "required": ["intent_id"],
                },
            ),
            # Utility
            Tool(
                name="memory_stats",
                description="Get memory statistics - counts of memories by type and layer.",
                inputSchema={"type": "object", "properties": {}},
            ),
            Tool(
                name="memory_list_warnings",
                description="List all warnings about fragile areas.",
                inputSchema={"type": "object", "properties": {}},
            ),
            Tool(
                name="memory_list_intents",
                description="List all active goals and intents.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "repo_id": {"type": "string", "description": "Repository/project ID"},
                        "status": {
                            "type": "string",
                            "enum": ["active", "completed", "closed", "all"],
                            "default": "active",
                            "description": "Intent status filter",
                        },
                    },
                },
            ),
            # Maintenance
            Tool(
                name="memory_compress",
                description="Compress old episodic memories into semantic knowledge.",
                inputSchema={"type": "object", "properties": {}},
            ),
            Tool(
                name="memory_decay",
                description="Apply decay to old, unused memories.",
                inputSchema={"type": "object", "properties": {}},
            ),
            Tool(
                name="memory_decay_preview",
                description=(
                    "Preview memory strength and which memories would decay without mutating them."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "repo_id": {"type": "string", "description": "Repository/project ID"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10},
                        "halflife_days": {
                            "type": "integer",
                            "minimum": 1,
                            "description": "Decay half-life override in days",
                        },
                        "min_importance": {
                            "type": "number",
                            "minimum": 0,
                            "maximum": 1,
                            "default": 0.1,
                            "description": "Minimum projected importance floor",
                        },
                    },
                },
            ),
            Tool(
                name="memory_clear_goals",
                description="Clear all active goals.",
                inputSchema={"type": "object", "properties": {}},
            ),
        ]

    # =========================================================================
    # Tool Handlers
    # =========================================================================

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        """Handle tool calls."""
        try:
            result = await handle_tool(name, arguments, memory)
            return [TextContent(type="text", text=result)]
        except Exception as e:
            logger.error(f"Error handling tool {name}: {e}")
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    # =========================================================================
    # Resources
    # =========================================================================

    @server.list_resources()
    async def list_resources() -> list[Resource]:
        """List available memory resources."""
        return [
            Resource(
                uri="memory://context",
                name="Project Memory Context",
                description="Full memory context with goals, warnings, and conventions",
                mimeType="text/markdown",
            ),
            Resource(
                uri="memory://warnings",
                name="Project Warnings",
                description="All warnings about fragile areas and gotchas",
                mimeType="text/plain",
            ),
            Resource(
                uri="memory://goals",
                name="Active Goals",
                description="Current active goals and intents",
                mimeType="text/plain",
            ),
            Resource(
                uri="memory://conventions",
                name="Project Conventions",
                description="Established project conventions and best practices",
                mimeType="text/plain",
            ),
            Resource(
                uri="memory://stats",
                name="Memory Statistics",
                description="Statistics about stored memories",
                mimeType="application/json",
            ),
            Resource(
                uri="memory://file/{path}",
                name="File Context",
                description="Get context for a specific file (use actual path, e.g., memory://file/src/auth.py)",
                mimeType="text/markdown",
            ),
            Resource(
                uri="memory://session",
                name="Current Session",
                description="Current session context and active work",
                mimeType="text/markdown",
            ),
        ]

    @server.read_resource()
    async def read_resource(uri: AnyUrl) -> str:
        """Read a memory resource."""
        uri_str = str(uri)
        if uri_str == "memory://context":
            return memory.context(format="text")

        elif uri_str == "memory://warnings":
            warnings = memory.semantic.get_warnings()
            if not warnings:
                return "No warnings."
            return "\n".join(f"- {w['content']}" for w in warnings)

        elif uri_str == "memory://goals":
            intents = memory.intent.get_active()
            if not intents:
                return "No active goals."
            priority_labels = {0: "LOW", 1: "NORMAL", 2: "HIGH", 3: "CRITICAL"}
            lines = []
            for i in intents:
                p = priority_labels.get(i.get("priority", 1), str(i.get("priority")))
                lines.append(f"[{p}] {i['description']}")
            return "\n".join(lines)

        elif uri_str == "memory://conventions":
            conventions = memory.semantic.get_conventions()
            if not conventions:
                return "No conventions established."
            return "\n".join(f"- {c['content']}" for c in conventions)

        elif uri_str == "memory://stats":
            return json.dumps(memory.stats(), indent=2)

        elif uri_str.startswith("memory://file/"):
            # Extract file path from URI
            file_path = uri_str.replace("memory://file/", "")

            from llm_memory.recall.proactive import ProactiveRecall

            recall = ProactiveRecall(memory)

            context = recall.on_file_open(file_path)
            formatted = recall.format_injection(context, format="markdown")

            return f"# Context for {file_path}\n\n{formatted}"

        elif uri_str == "memory://session":
            # Get current session context
            summary = memory.intent.summarize()

            lines = ["# Current Session\n"]

            if summary.get("current_task"):
                lines.append(f"**Current Task:** {summary['current_task']['description']}\n")

            if summary.get("focus"):
                lines.append(f"**Focus:** {summary['focus']['description']}\n")

            if summary.get("constraints"):
                lines.append("\n**Constraints:**")
                for c in summary["constraints"]:
                    lines.append(f"- {c}")
                lines.append("")

            # Add recent activity
            recent = memory.episodic.recent(limit=5)
            if recent:
                lines.append("\n**Recent Activity:**")
                for r in recent:
                    lines.append(f"- [{r['category']}] {r['content'][:100]}")

            return "\n".join(lines)

        else:
            raise ValueError(f"Unknown resource: {uri_str}")

    # =========================================================================
    # Prompts
    # =========================================================================

    @server.list_prompts()
    async def list_prompts() -> list[Prompt]:
        """List available prompts."""
        return [
            Prompt(
                name="start_session",
                description="Load project context at the start of a work session",
                arguments=[],
            ),
            Prompt(
                name="before_change",
                description="Get relevant warnings before modifying files",
                arguments=[
                    PromptArgument(
                        name="files",
                        description="Comma-separated list of files to check",
                        required=True,
                    )
                ],
            ),
            Prompt(
                name="end_session",
                description="Record session summary before ending work",
                arguments=[
                    PromptArgument(
                        name="summary",
                        description="What was accomplished this session",
                        required=True,
                    )
                ],
            ),
            Prompt(
                name="codex_workflow",
                description="Use llm-memory effectively during a Codex coding session",
                arguments=[],
            ),
        ]

    @server.get_prompt()
    async def get_prompt(name: str, arguments: dict[str, str] | None) -> GetPromptResult:
        """Get a prompt by name."""
        if name == "start_session":
            context = memory.context(format="text")
            return GetPromptResult(
                description="Project memory context for starting work",
                messages=[
                    PromptMessage(
                        role="user",
                        content=TextContent(
                            type="text", text=f"Here is the project memory context:\n\n{context}"
                        ),
                    )
                ],
            )

        elif name == "before_change":
            files = (arguments or {}).get("files", "").split(",")
            files = [f.strip() for f in files if f.strip()]
            relevant = memory.relevant_for(files=files)

            output = ["**Before modifying these files, note:**\n"]
            if relevant["warnings"]:
                output.append("**Warnings:**")
                for w in relevant["warnings"]:
                    output.append(f"  - {w['content']}")
            if relevant["knowledge"]:
                output.append("\n**Relevant knowledge:**")
                for k in relevant["knowledge"][:5]:
                    output.append(f"  - {k['content']}")
            if not relevant["warnings"] and not relevant["knowledge"]:
                output.append("No specific warnings or knowledge for these files.")

            return GetPromptResult(
                description="Warnings and knowledge for files",
                messages=[
                    PromptMessage(
                        role="user", content=TextContent(type="text", text="\n".join(output))
                    )
                ],
            )

        elif name == "end_session":
            summary = (arguments or {}).get("summary", "Session ended")
            return GetPromptResult(
                description="End session prompt",
                messages=[
                    PromptMessage(
                        role="user",
                        content=TextContent(
                            type="text",
                            text=(
                                f"Session summary: {summary}\n\n"
                                "Please record this as an event and any learnings as knowledge."
                            ),
                        ),
                    )
                ],
            )

        elif name == "codex_workflow":
            return GetPromptResult(
                description="Codex memory workflow",
                messages=[
                    PromptMessage(
                        role="user",
                        content=TextContent(
                            type="text",
                            text=(
                                "For this Codex session, use llm-memory as durable project "
                                "context. At the start, call memory_session_start or "
                                "memory_context. Before editing files, call memory_before_change "
                                "with the task and file list. After meaningful work, call "
                                "memory_after_work to record bugs, decisions, conventions, and "
                                "fragile areas that future sessions should remember."
                            ),
                        ),
                    )
                ],
            )

        else:
            raise ValueError(f"Unknown prompt: {name}")

    return server


def _handle_context(args: dict[str, Any], memory: Memory) -> str:
    """Handle context tools."""
    fmt = args.get("format", "text")
    include_history = args.get("include_history", True)
    ctx = memory.context(format=fmt, include_history=include_history)
    if fmt == "json":
        return json.dumps(ctx, indent=2, default=str)
    return ctx


def _handle_search(name: str, args: dict[str, Any], memory: Memory) -> str:
    """Handle search tools."""
    if name == "memory_recall":
        results = memory.recall(
            query=args["query"], limit=args.get("limit", 10), repo_id=args.get("repo_id")
        )
        if not results:
            return "No memories found matching query."

        output = [f"Found {len(results)} memories:\n"]
        for r in results:
            sim = f" (similarity: {r.get('similarity', 0):.2f})" if r.get("similarity") else ""
            output.append(f"- [{r['layer']}/{r.get('category', 'unknown')}]{sim}: {r['content']}")
        return "\n".join(output)

    elif name == "memory_remember":
        memories = memory._storage.list_memories(
            repo_id=args.get("repo_id") or memory.config.repo_id,
            layer=args.get("layer"),
            category=args.get("category"),
            status="active",
            limit=1,
            order_by="created_at DESC",
        )
        if not memories:
            return "No latest memory found."
        latest = memories[0]
        return (
            "Latest memory:\n"
            f"- ID: {latest['id']}\n"
            f"- Layer: {latest.get('layer', 'unknown')}\n"
            f"- Category: {latest.get('category', 'unknown')}\n"
            f"- Created: {latest.get('created_at')}\n"
            f"- Content: {latest.get('content', '')}"
        )

    elif name == "memory_relevant":
        relevant = memory.relevant_for(task=args.get("task"), files=args.get("files"))

        output = []
        if relevant["warnings"]:
            output.append("**Warnings:**")
            for w in relevant["warnings"]:
                output.append(f"  - {w['content']}")

        if relevant["knowledge"]:
            output.append("\n**Relevant Knowledge:**")
            for k in relevant["knowledge"][:5]:
                output.append(f"  - {k['content']}")

        if relevant["history"]:
            output.append("\n**Related History:**")
            for h in relevant["history"][:3]:
                output.append(f"  - {h['content']}")

        return "\n".join(output) if output else "No relevant memories found."

    return f"Unknown search tool: {name}"


def _handle_proactive(name: str, args: dict[str, Any], memory: Memory) -> str:
    """Handle proactive recall tools."""
    if name == "memory_file_context":
        from llm_memory.recall.proactive import ProactiveRecall

        recall = ProactiveRecall(memory)

        context = recall.on_file_open(
            file_path=args["file_path"], include_related=args.get("include_related", True)
        )

        formatted = recall.format_injection(context, format="markdown")
        return formatted if formatted.strip() else "No context found for this file."

    elif name == "memory_find_error":
        from llm_memory.recall.proactive import ProactiveRecall

        recall = ProactiveRecall(memory)

        similar = recall.on_error(
            error_message=args["error_message"],
            error_type=args.get("error_type"),
            file_path=args.get("file_path"),
            limit=args.get("limit", 5),
        )

        if not similar:
            return "No similar errors found."

        output = [f"Found {len(similar)} similar errors:\n"]
        for i, err in enumerate(similar, 1):
            similarity = err.get("similarity", 0)
            content = err["content"][:200]
            output.append(f"{i}. [Similarity: {similarity:.2f}] {content}")

            if "fix" in content.lower() or "solution" in content.lower():
                output.append("   ✓ Contains fix/solution")

        return "\n".join(output)

    elif name == "memory_directory_context":
        from llm_memory.recall.proactive import ProactiveRecall

        recall = ProactiveRecall(memory)

        context = recall.on_directory(
            dir_path=args["dir_path"], recursive=args.get("recursive", False)
        )

        formatted = recall.format_injection(context, format="markdown")
        return formatted if formatted.strip() else "No context found for this directory."

    return f"Unknown proactive tool: {name}"


def _format_relevant_memory(relevant: dict[str, list[dict[str, Any]]]) -> str:
    output = []
    if relevant.get("warnings"):
        output.append("**Warnings:**")
        for w in relevant["warnings"]:
            output.append(f"  - {w['content']}")

    if relevant.get("knowledge"):
        output.append("\n**Relevant Knowledge:**")
        for k in relevant["knowledge"][:8]:
            output.append(f"  - {k['content']}")

    if relevant.get("history"):
        output.append("\n**Related History:**")
        for h in relevant["history"][:5]:
            output.append(f"  - {h['content']}")

    return "\n".join(output) if output else "No relevant memories found."


def _handle_workflow(name: str, args: dict[str, Any], memory: Memory) -> str:
    """Handle Codex-style recall-before-work and record-after-work tools."""
    if name == "memory_session_start":
        task = args.get("task")
        files = args.get("files")
        repo_id = args.get("repo_id")

        lines = ["# LLM Memory Session Context"]
        if task:
            intent_id = memory.working_on(task=task, files=files, repo_id=repo_id)
            lines.append(f"\nCurrent task recorded (ID: {intent_id}): {task}")

        context = memory.context(format="text", include_history=True)
        lines.append(f"\n{context}")

        if task or files:
            relevant = memory.relevant_for(task=task, files=files)
            lines.append("\n## Task-Relevant Memory")
            lines.append(_format_relevant_memory(relevant))

        return "\n".join(lines)

    if name == "memory_before_change":
        task = args.get("task")
        files = args.get("files")
        relevant = memory.relevant_for(task=task, files=files)
        header = "# Before Changing Code"
        if files:
            header += f"\nFiles: {', '.join(files)}"
        if task:
            header += f"\nTask: {task}"
        return f"{header}\n\n{_format_relevant_memory(relevant)}"

    if name == "memory_after_work":
        repo_id = args.get("repo_id")
        summary_id = memory.record(
            event=args["summary"],
            category=args.get("category", "note"),
            importance=0.7,
            repo_id=repo_id,
        )
        recorded = [f"Recorded summary (ID: {summary_id})"]

        for decision in args.get("decisions") or []:
            decision_id = memory.decision(
                what=decision,
                why="Recorded from Codex end-of-work summary.",
                repo_id=repo_id,
            )
            recorded.append(f"Recorded decision (ID: {decision_id}): {decision}")

        for bug in args.get("bugs_fixed") or []:
            bug_id = memory.record(
                event=bug,
                category="bug_fixed",
                importance=0.8,
                repo_id=repo_id,
            )
            recorded.append(f"Recorded bug fix (ID: {bug_id}): {bug}")

        for item in args.get("warnings") or []:
            warning_id = memory.warn(
                area=item["area"],
                warning=item["warning"],
                severity=0.8,
                repo_id=repo_id,
            )
            recorded.append(f"Recorded warning (ID: {warning_id}): {item['area']}")

        return "\n".join(recorded)

    return f"Unknown workflow tool: {name}"


def _handle_recording(name: str, args: dict[str, Any], memory: Memory) -> str:
    """Handle recording tools."""
    if name == "memory_record":
        mem_id = memory.record(
            event=args["event"],
            category=args.get("category", "note"),
            importance=args.get("importance", 0.5),
            repo_id=args.get("repo_id"),
        )
        return f"Recorded event (ID: {mem_id}): {args['event']}"

    elif name == "memory_decision":
        mem_id = memory.decision(
            what=args["what"],
            why=args["why"],
            alternatives=args.get("alternatives"),
            repo_id=args.get("repo_id"),
        )
        return f"Decision recorded (ID: {mem_id}): {args['what']}"

    return f"Unknown recording tool: {name}"


def _handle_knowledge(name: str, args: dict[str, Any], memory: Memory) -> str:
    """Handle knowledge tools."""
    if name == "memory_learn":
        mem_id = memory.learn(
            knowledge=args["knowledge"],
            category=args.get("category", "fact"),
            importance=args.get("importance", 0.6),
            repo_id=args.get("repo_id"),
            detect_conflicts=args.get("detect_conflicts", False),
        )
        return f"Knowledge established (ID: {mem_id}): {args['knowledge']}"

    elif name == "memory_warn":
        mem_id = memory.warn(
            area=args["area"],
            warning=args["warning"],
            severity=args.get("severity", 0.7),
            repo_id=args.get("repo_id"),
        )
        return f"Warning added for {args['area']}: {args['warning']}"

    elif name == "memory_issue":
        mem_id = memory.semantic.known_issue(
            issue=args["issue"],
            workaround=args.get("workaround"),
            priority=args.get("priority", 0.5),
            repo_id=args.get("repo_id"),
        )
        return f"Known issue documented: {args['issue']}"

    return f"Unknown knowledge tool: {name}"


def _handle_intent(name: str, args: dict[str, Any], memory: Memory) -> str:
    """Handle intent tools."""
    if name == "memory_goal":
        intent_id = memory.goal(
            goal=args["goal"],
            priority=args.get("priority", 1),
            constraints=args.get("constraints"),
            repo_id=args.get("repo_id"),
        )
        return f"Goal set (ID: {intent_id}): {args['goal']}"

    elif name == "memory_working_on":
        intent_id = memory.working_on(
            task=args["task"], files=args.get("files"), repo_id=args.get("repo_id")
        )
        return f"Working on: {args['task']}"

    elif name == "memory_done":
        cleared = memory.done()
        return f"Cleared {cleared} task(s)"

    elif name == "memory_update_intent":
        update_data = {
            key: args[key]
            for key in ("description", "priority", "status")
            if key in args and args[key] is not None
        }
        if not update_data:
            return "No intent fields provided to update."
        updated = memory.intent.update(args["intent_id"], **update_data)
        if not updated:
            return f"Intent not found: {args['intent_id']}"
        return f"Intent updated: {args['intent_id']}"

    elif name == "memory_close_intent":
        closed = memory.intent.close(args["intent_id"])
        if not closed:
            return f"Intent not found: {args['intent_id']}"
        return f"Intent closed: {args['intent_id']}"

    return f"Unknown intent tool: {name}"


def _parse_memory_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    return datetime.now()


def _format_decay_preview(args: dict[str, Any], memory: Memory) -> str:
    repo_id = args.get("repo_id") or memory.config.repo_id
    limit = max(1, min(int(args.get("limit", 10)), 100))
    halflife_days = max(1, int(args.get("halflife_days") or memory.config.decay_halflife_days))
    min_importance = max(0.0, min(float(args.get("min_importance", 0.1)), 1.0))
    memories = memory._storage.list_memories(
        repo_id=repo_id,
        status="active",
        limit=10000,
        order_by="accessed_at ASC",
    )
    now = datetime.now()
    previews = []
    for item in memories:
        current = float(item.get("importance", 0.5) or 0.0)
        accessed = _parse_memory_datetime(item.get("accessed_at") or item.get("created_at"))
        age_days = max(0.0, (now - accessed).total_seconds() / 86400)
        projected = max(min_importance, current * (0.5 ** (age_days / halflife_days)))
        decay_amount = max(0.0, current - projected)
        if current <= min_importance + 0.001:
            risk = "at_floor"
        elif projected <= min_importance + 0.05 or decay_amount >= 0.2:
            risk = "likely_to_decay"
        elif decay_amount >= 0.05:
            risk = "weakening"
        else:
            risk = "stable"
        previews.append((risk, decay_amount, age_days, current, projected, item))

    risk_rank = {"likely_to_decay": 0, "weakening": 1, "at_floor": 2, "stable": 3}
    previews.sort(key=lambda row: (risk_rank[row[0]], -row[1], row[4], -row[2]))
    if not previews:
        return "No active memories found for decay preview."

    lines = [
        (
            f"Decay preview: halflife={halflife_days}d, "
            f"min_importance={min_importance:.2f}, decay_enabled={memory.config.decay_enabled}"
        )
    ]
    for risk, decay_amount, age_days, current, projected, item in previews[:limit]:
        snippet = " ".join(str(item.get("content", "")).split())[:100]
        lines.append(
            f"- [{risk}] {item['id']}: {current:.2f} -> {projected:.2f} "
            f"(drop {decay_amount:.2f}, idle {age_days:.1f}d) {snippet}"
        )
    return "\n".join(lines)


def _handle_utility(name: str, args: dict[str, Any], memory: Memory) -> str:
    """Handle utility tools."""
    if name == "memory_stats":
        stats = memory.stats()
        return json.dumps(stats, indent=2)

    elif name == "memory_list_warnings":
        warnings = memory.semantic.get_warnings()
        if not warnings:
            return "No warnings."
        return "\n".join(f"- {w['content']}" for w in warnings)

    elif name == "memory_list_intents":
        intents = memory._storage.get_active_intents(
            repo_id=args.get("repo_id"),
            status=args.get("status", "active"),
        )
        if not intents:
            return "No active intents."

        priority_labels = {0: "LOW", 1: "NORMAL", 2: "HIGH", 3: "CRITICAL"}
        output = []
        for i in intents:
            p = priority_labels.get(i.get("priority", 1), str(i.get("priority")))
            output.append(f"- [{p}] {i['description']}")
        return "\n".join(output)

    return f"Unknown utility tool: {name}"


def _handle_maintenance(name: str, args: dict[str, Any], memory: Memory) -> str:
    """Handle maintenance tools."""
    if name == "memory_compress":
        created = memory.compress()
        return f"Compression complete. Created {len(created)} semantic memories."

    elif name == "memory_decay":
        affected = memory.decay()
        return f"Decay applied to {affected} memories."

    elif name == "memory_decay_preview":
        return _format_decay_preview(args, memory)

    elif name == "memory_clear_goals":
        cleared = memory.intent.clear_all()
        return f"Cleared {cleared} goals."

    return f"Unknown maintenance tool: {name}"


async def handle_tool(name: str, args: dict[str, Any], memory: Memory) -> str:
    """Route tool calls to specialized handlers."""

    # Context
    if name == "memory_context":
        return _handle_context(args, memory)

    # Search
    if name in ["memory_recall", "memory_remember", "memory_relevant"]:
        return _handle_search(name, args, memory)

    # Proactive
    if name in ["memory_file_context", "memory_find_error", "memory_directory_context"]:
        return _handle_proactive(name, args, memory)

    # Codex workflow
    if name in ["memory_session_start", "memory_before_change", "memory_after_work"]:
        return _handle_workflow(name, args, memory)

    # Recording
    if name in ["memory_record", "memory_decision"]:
        return _handle_recording(name, args, memory)

    # Knowledge
    if name in ["memory_learn", "memory_warn", "memory_issue"]:
        return _handle_knowledge(name, args, memory)

    # Intent
    if name in [
        "memory_goal",
        "memory_working_on",
        "memory_done",
        "memory_update_intent",
        "memory_close_intent",
    ]:
        return _handle_intent(name, args, memory)

    # Utility
    if name in ["memory_stats", "memory_list_warnings", "memory_list_intents"]:
        return _handle_utility(name, args, memory)

    # Maintenance
    if name in ["memory_compress", "memory_decay", "memory_decay_preview", "memory_clear_goals"]:
        return _handle_maintenance(name, args, memory)

    return f"Unknown tool: {name}"


async def run_server():
    """Run the MCP server."""
    if not MCP_AVAILABLE:
        print("Error: MCP package not installed.")
        print("Install with: pip install llm-memory[mcp]")
        return

    server = create_mcp_server()

    async with stdio_server() as (read_stream, write_stream):
        logger.info("LLM Memory MCP server starting...")
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main():
    """Entry point for MCP server."""
    import asyncio

    asyncio.run(run_server())


if __name__ == "__main__":
    main()
