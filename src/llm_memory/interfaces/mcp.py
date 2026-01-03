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
        raise ImportError(
            "MCP package not installed. Install with: pip install llm-memory[mcp]"
        )

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
                            "description": "Output format"
                        },
                        "include_history": {
                            "type": "boolean",
                            "default": True,
                            "description": "Include recent events"
                        }
                    }
                }
            ),
            Tool(
                name="memory_recall",
                description="Search memories for past decisions, knowledge, and events.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "What to search for (natural language)"
                        },
                        "limit": {
                            "type": "integer",
                            "default": 10,
                            "description": "Maximum results to return"
                        }
                    },
                    "required": ["query"]
                }
            ),
            Tool(
                name="memory_relevant",
                description="Get memories relevant to specific files or a task.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "task": {
                            "type": "string",
                            "description": "Task description"
                        },
                        "files": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Files being worked on"
                        }
                    }
                }
            ),

            # Proactive Recall
            Tool(
                name="memory_file_context",
                description="Get proactive context for a specific file (warnings, bugs, decisions, knowledge).",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "Path to the file"
                        },
                        "include_related": {
                            "type": "boolean",
                            "default": True,
                            "description": "Include related files"
                        }
                    },
                    "required": ["file_path"]
                }
            ),
            Tool(
                name="memory_find_error",
                description="Find similar past errors with fixes and workarounds.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "error_message": {
                            "type": "string",
                            "description": "The error message to search for"
                        },
                        "error_type": {
                            "type": "string",
                            "description": "Optional error type (e.g., TypeError, ValueError)"
                        },
                        "file_path": {
                            "type": "string",
                            "description": "Optional file where error occurred"
                        },
                        "limit": {
                            "type": "integer",
                            "default": 5,
                            "description": "Maximum similar errors to return"
                        }
                    },
                    "required": ["error_message"]
                }
            ),
            Tool(
                name="memory_directory_context",
                description="Get aggregated knowledge for an entire directory.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "dir_path": {
                            "type": "string",
                            "description": "Directory path"
                        },
                        "recursive": {
                            "type": "boolean",
                            "default": False,
                            "description": "Include subdirectories"
                        }
                    },
                    "required": ["dir_path"]
                }
            ),

            # Recording Events
            Tool(
                name="memory_record",
                description="Record an event (bug fix, change, discovery).",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "event": {
                            "type": "string",
                            "description": "What happened"
                        },
                        "category": {
                            "type": "string",
                            "enum": [
                                "note", "bug_fixed", "bug_found", "feature_added",
                                "refactor", "discovery", "incident", "architecture_decision",
                                "trade_off", "design_choice", "investigation", "experiment"
                            ],
                            "default": "note",
                            "description": "Type of event"
                        },
                        "importance": {
                            "type": "number",
                            "minimum": 0,
                            "maximum": 1,
                            "default": 0.5,
                            "description": "How important (0.0-1.0)"
                        }
                    },
                    "required": ["event"]
                }
            ),
            Tool(
                name="memory_decision",
                description="Record an architecture or design decision with reasoning.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "what": {
                            "type": "string",
                            "description": "What was decided"
                        },
                        "why": {
                            "type": "string",
                            "description": "Why this choice was made"
                        },
                        "alternatives": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Alternatives that were considered"
                        }
                    },
                    "required": ["what", "why"]
                }
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
                            "description": "The knowledge/fact/pattern"
                        },
                        "category": {
                            "type": "string",
                            "enum": [
                                "fact", "invariant", "behavior", "pattern", "convention",
                                "best_practice", "contract", "antipattern", "gotcha"
                            ],
                            "default": "fact",
                            "description": "Type of knowledge"
                        },
                        "importance": {
                            "type": "number",
                            "minimum": 0,
                            "maximum": 1,
                            "default": 0.6,
                            "description": "How important (0.0-1.0)"
                        }
                    },
                    "required": ["knowledge"]
                }
            ),
            Tool(
                name="memory_warn",
                description="Add a warning about a fragile or dangerous area.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "area": {
                            "type": "string",
                            "description": "Area/file/module to warn about"
                        },
                        "warning": {
                            "type": "string",
                            "description": "What to watch out for"
                        },
                        "severity": {
                            "type": "number",
                            "minimum": 0,
                            "maximum": 1,
                            "default": 0.7,
                            "description": "How serious (0.0-1.0)"
                        }
                    },
                    "required": ["area", "warning"]
                }
            ),
            Tool(
                name="memory_issue",
                description="Document a known issue with optional workaround.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "issue": {
                            "type": "string",
                            "description": "Issue description"
                        },
                        "workaround": {
                            "type": "string",
                            "description": "How to work around it"
                        },
                        "priority": {
                            "type": "number",
                            "minimum": 0,
                            "maximum": 1,
                            "default": 0.5,
                            "description": "Priority to fix (0.0-1.0)"
                        }
                    },
                    "required": ["issue"]
                }
            ),

            # Intent Management
            Tool(
                name="memory_goal",
                description="Set a goal or intent with optional constraints.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "goal": {
                            "type": "string",
                            "description": "Goal description"
                        },
                        "priority": {
                            "type": "integer",
                            "enum": [0, 1, 2, 3],
                            "default": 1,
                            "description": "Priority: 0=low, 1=normal, 2=high, 3=critical"
                        },
                        "constraints": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Constraints to respect"
                        }
                    },
                    "required": ["goal"]
                }
            ),
            Tool(
                name="memory_working_on",
                description="Set what is currently being worked on. Helps track context.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "task": {
                            "type": "string",
                            "description": "What you're working on"
                        },
                        "files": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Files being modified"
                        }
                    },
                    "required": ["task"]
                }
            ),
            Tool(
                name="memory_done",
                description="Clear current task (mark as done).",
                inputSchema={
                    "type": "object",
                    "properties": {}
                }
            ),

            # Utility
            Tool(
                name="memory_stats",
                description="Get memory statistics - counts of memories by type and layer.",
                inputSchema={
                    "type": "object",
                    "properties": {}
                }
            ),
            Tool(
                name="memory_list_warnings",
                description="List all warnings about fragile areas.",
                inputSchema={
                    "type": "object",
                    "properties": {}
                }
            ),
            Tool(
                name="memory_list_intents",
                description="List all active goals and intents.",
                inputSchema={
                    "type": "object",
                    "properties": {}
                }
            ),

            # Maintenance
            Tool(
                name="memory_compress",
                description="Compress old episodic memories into semantic knowledge.",
                inputSchema={
                    "type": "object",
                    "properties": {}
                }
            ),
            Tool(
                name="memory_decay",
                description="Apply decay to old, unused memories.",
                inputSchema={
                    "type": "object",
                    "properties": {}
                }
            ),
            Tool(
                name="memory_clear_goals",
                description="Clear all active goals.",
                inputSchema={
                    "type": "object",
                    "properties": {}
                }
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
                mimeType="text/markdown"
            ),
            Resource(
                uri="memory://warnings",
                name="Project Warnings",
                description="All warnings about fragile areas and gotchas",
                mimeType="text/plain"
            ),
            Resource(
                uri="memory://goals",
                name="Active Goals",
                description="Current active goals and intents",
                mimeType="text/plain"
            ),
            Resource(
                uri="memory://conventions",
                name="Project Conventions",
                description="Established project conventions and best practices",
                mimeType="text/plain"
            ),
            Resource(
                uri="memory://stats",
                name="Memory Statistics",
                description="Statistics about stored memories",
                mimeType="application/json"
            ),
            Resource(
                uri="memory://file/{path}",
                name="File Context",
                description="Get context for a specific file (use actual path, e.g., memory://file/src/auth.py)",
                mimeType="text/markdown"
            ),
            Resource(
                uri="memory://session",
                name="Current Session",
                description="Current session context and active work",
                mimeType="text/markdown"
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
                p = priority_labels.get(i.get('priority', 1), str(i.get('priority')))
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
                arguments=[]
            ),
            Prompt(
                name="before_change",
                description="Get relevant warnings before modifying files",
                arguments=[
                    PromptArgument(
                        name="files",
                        description="Comma-separated list of files to check",
                        required=True
                    )
                ]
            ),
            Prompt(
                name="end_session",
                description="Record session summary before ending work",
                arguments=[
                    PromptArgument(
                        name="summary",
                        description="What was accomplished this session",
                        required=True
                    )
                ]
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
                            type="text",
                            text=f"Here is the project memory context:\n\n{context}"
                        )
                    )
                ]
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
                        role="user",
                        content=TextContent(type="text", text="\n".join(output))
                    )
                ]
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
                            )
                        )
                    )
                ]
            )

        else:
            raise ValueError(f"Unknown prompt: {name}")

    return server


async def handle_tool(name: str, args: dict[str, Any], memory: Memory) -> str:
    """Route tool calls to memory methods."""

    # Context & Search
    if name == "memory_context":
        fmt = args.get("format", "text")
        include_history = args.get("include_history", True)
        ctx = memory.context(
            format=fmt,
            include_history=include_history
        )
        if fmt == "json":
            return json.dumps(ctx, indent=2, default=str)
        return ctx

    elif name == "memory_recall":
        results = memory.recall(
            query=args["query"],
            limit=args.get("limit", 10)
        )
        if not results:
            return "No memories found matching query."

        output = [f"Found {len(results)} memories:\n"]
        for r in results:
            sim = f" (similarity: {r.get('similarity', 0):.2f})" if r.get('similarity') else ""
            output.append(f"- [{r['layer']}/{r.get('category', 'unknown')}]{sim}: {r['content']}")
        return "\n".join(output)

    elif name == "memory_relevant":
        relevant = memory.relevant_for(
            task=args.get("task"),
            files=args.get("files")
        )

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

    # Proactive Recall
    elif name == "memory_file_context":
        from llm_memory.recall.proactive import ProactiveRecall
        recall = ProactiveRecall(memory)

        context = recall.on_file_open(
            file_path=args["file_path"],
            include_related=args.get("include_related", True)
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
            limit=args.get("limit", 5)
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
            dir_path=args["dir_path"],
            recursive=args.get("recursive", False)
        )

        formatted = recall.format_injection(context, format="markdown")
        return formatted if formatted.strip() else "No context found for this directory."

    # Recording Events
    elif name == "memory_record":
        mem_id = memory.record(
            event=args["event"],
            category=args.get("category", "note"),
            importance=args.get("importance", 0.5)
        )
        return f"Recorded event (ID: {mem_id}): {args['event']}"

    elif name == "memory_decision":
        mem_id = memory.decision(
            what=args["what"],
            why=args["why"],
            alternatives=args.get("alternatives")
        )
        return f"Decision recorded (ID: {mem_id}): {args['what']}"

    # Establishing Knowledge
    elif name == "memory_learn":
        mem_id = memory.learn(
            knowledge=args["knowledge"],
            category=args.get("category", "fact"),
            importance=args.get("importance", 0.6)
        )
        return f"Knowledge established (ID: {mem_id}): {args['knowledge']}"

    elif name == "memory_warn":
        mem_id = memory.warn(
            area=args["area"],
            warning=args["warning"],
            severity=args.get("severity", 0.7)
        )
        return f"Warning added for {args['area']}: {args['warning']}"

    elif name == "memory_issue":
        mem_id = memory.semantic.known_issue(
            issue=args["issue"],
            workaround=args.get("workaround"),
            priority=args.get("priority", 0.5)
        )
        return f"Known issue documented: {args['issue']}"

    # Intent Management
    elif name == "memory_goal":
        intent_id = memory.goal(
            goal=args["goal"],
            priority=args.get("priority", 1),
            constraints=args.get("constraints")
        )
        return f"Goal set (ID: {intent_id}): {args['goal']}"

    elif name == "memory_working_on":
        intent_id = memory.working_on(
            task=args["task"],
            files=args.get("files")
        )
        return f"Working on: {args['task']}"

    elif name == "memory_done":
        cleared = memory.done()
        return f"Cleared {cleared} task(s)"

    # Utility
    elif name == "memory_stats":
        stats = memory.stats()
        return json.dumps(stats, indent=2)

    elif name == "memory_list_warnings":
        warnings = memory.semantic.get_warnings()
        if not warnings:
            return "No warnings."
        return "\n".join(f"- {w['content']}" for w in warnings)

    elif name == "memory_list_intents":
        intents = memory.intent.get_active()
        if not intents:
            return "No active intents."

        priority_labels = {0: "LOW", 1: "NORMAL", 2: "HIGH", 3: "CRITICAL"}
        output = []
        for i in intents:
            p = priority_labels.get(i.get('priority', 1), str(i.get('priority')))
            output.append(f"- [{p}] {i['description']}")
        return "\n".join(output)

    # Maintenance
    elif name == "memory_compress":
        created = memory.compress()
        return f"Compression complete. Created {len(created)} semantic memories."

    elif name == "memory_decay":
        affected = memory.decay()
        return f"Decay applied to {affected} memories."

    elif name == "memory_clear_goals":
        cleared = memory.intent.clear_all()
        return f"Cleared {cleared} goals."

    else:
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
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options()
        )


def main():
    """Entry point for MCP server."""
    import asyncio
    asyncio.run(run_server())


if __name__ == "__main__":
    main()
