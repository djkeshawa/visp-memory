"""
MCP (Model Context Protocol) Server for Visp Memory.

Exposes the memory system as MCP tools that LLMs can call directly.

Usage:
    # Run the MCP stdio server (the `serve` command runs the FastAPI/REST
    # server instead — it is NOT the MCP server).
    visp-memory-mcp

    # Or run the module directly
    python -m visp_memory.interfaces.mcp

Configuration for Claude Desktop (claude_desktop_config.json):
    {
      "mcpServers": {
        "visp-memory": {
          "command": "python",
          "args": ["-m", "visp_memory.interfaces.mcp"],
          "cwd": "/path/to/your/project"
        }
      }
    }

Or with uvx:
    {
      "mcpServers": {
        "visp-memory": {
          "command": "uvx",
          "args": ["--from", "visp-memory", "visp-memory-mcp"],
          "cwd": "/path/to/your/project"
        }
      }
    }
"""

import json
import logging
import os
from datetime import datetime
from typing import Any

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import (
        GetPromptResult,
        Prompt,
        PromptArgument,
        PromptMessage,
        Resource,
        SamplingMessage,
        TextContent,
        Tool,
    )

    # `AnyUrl` is a pydantic type that mcp.types re-exported up to 1.x and dropped in
    # 2.0. Importing it from mcp.types made the whole block raise ImportError on mcp
    # 2.x, silently setting MCP_AVAILABLE = False -- so a fresh `pip install
    # visp-memory[mcp]` produced a server that reported itself as unavailable. It is
    # only used as a type annotation, so take it from pydantic, which is a hard
    # dependency and the original source in both versions.
    from pydantic import AnyUrl

    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False

from visp_memory import Memory
from visp_memory.core.clock import parse_utc, utc_now
from visp_memory.core.eligibility import UNSCOPED_REPO_ID, require_repo_id
from visp_memory.core.ranking import projected_importance
from visp_memory.core.trust import WriteChannel

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("visp-memory-mcp")

# MCP schema enums are advisory-only in this SDK: a client can send any value
# regardless of the declared enum. Validation therefore has to happen server-side
# in the handlers below. These allow-lists are the canonical sets.
VALID_LAYERS = frozenset({"raw", "episodic", "semantic", "intent"})
VALID_INTENT_STATUSES = frozenset({"active", "completed", "closed"})
RUNTIME_SCOPE_SCHEMA = {
    "oneOf": [
        {"type": "string"},
        {"type": "array", "items": {"type": "string"}},
    ]
}

# Every MCP tool definition (name + description + input schema) is loaded into the
# assistant's context on every session. With the full surface that is several
# thousand tokens of overhead before any work begins - at odds with this project's
# token-efficiency goal. The "core" profile exposes only the tools an assistant
# needs for the everyday recall-before-work / record-after-work loop, roughly
# halving that overhead, while "full" keeps every advanced and maintenance tool.
# Hidden tools remain fully functional if a client calls them by name; the profile
# only controls what is advertised.
#
# Default is "core". A memory server that costs several thousand context tokens before
# the assistant does any work is arguing against its own premise, and an advertised
# surface of 36 tools measurably degrades tool selection. Operators who want the full
# maintenance/admin surface set VISP_MEMORY_MCP_PROFILE=full.
CORE_TOOL_NAMES = frozenset(
    {
        # Context & search
        "memory_prepare_task",
        "memory_context",
        "memory_recall",
        "memory_trace",
        # Proactive recall
        "memory_file_context",
        "memory_find_error",
        # Recall-before-work / record-after-work loop
        "memory_session_start",
        "memory_before_change",
        "memory_after_work",
        # Capture primitives
        "memory_record",
        "memory_decision",
        "memory_learn",
        "memory_warn",
        # Intent
        "memory_goal",
        "memory_working_on",
        "memory_done",
        # Feedback — required for the reinforcement loop ("use it or lose it"): without
        # this tool a core-profile client can only emit non-reinforcing "surfaced" events.
        "memory_feedback_log",
    }
)

# The read-only subset of the core loop: retrieval and context only. None of
# these mutate memory state (P10-US-07). Feedback logging is excluded — it
# writes reinforcement events.
READONLY_TOOL_NAMES = frozenset(
    {
        "memory_prepare_task",
        "memory_context",
        "memory_recall",
        "memory_trace",
        "memory_file_context",
        "memory_find_error",
    }
)

VALID_MCP_PROFILES = frozenset({"core", "full", "readonly"})


def _resolve_tool_profile() -> str:
    """Return the configured MCP tool profile ("core", "full" or "readonly")."""
    raw = os.environ.get("VISP_MEMORY_MCP_PROFILE", "").strip()
    profile = raw.lower()
    if profile in VALID_MCP_PROFILES:
        return profile
    if raw:
        # Never fail open in silence. These values gate a restriction: an
        # operator who types "read-only" intends six read tools and would
        # otherwise silently receive seventeen, writers included. The typo is
        # theirs; the silence would have been ours.
        logger.warning(
            "VISP_MEMORY_MCP_PROFILE=%r is not a recognised profile (%s); using 'core' instead.",
            raw,
            ", ".join(sorted(VALID_MCP_PROFILES)),
        )
    return "core"


def _profile_tool_names(profile: str, all_names: frozenset[str]) -> frozenset[str]:
    """The tool names a profile permits — used by BOTH advertisement and dispatch."""
    if profile == "full":
        return all_names
    if profile == "readonly":
        return READONLY_TOOL_NAMES
    return CORE_TOOL_NAMES


def _filter_tools_by_profile(tools: list["Tool"], profile: str) -> list["Tool"]:
    """Restrict advertised tools to the active profile."""
    if profile == "full":
        return tools
    allowed = _profile_tool_names(profile, frozenset(tool.name for tool in tools))
    return [tool for tool in tools if tool.name in allowed]


def create_mcp_server() -> "Server":
    """Create and configure the MCP server."""
    if not MCP_AVAILABLE:
        raise ImportError("MCP package not installed. Install with: pip install visp-memory[mcp]")

    server = Server("visp-memory")
    memory = Memory()

    # =========================================================================
    # Tool Definitions
    # =========================================================================

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        """List available memory tools, scoped to the active profile."""
        all_tools = [
            # Context & Search
            Tool(
                name="memory_prepare_task",
                description=(
                    "Prepare a cited, token-budgeted task brief with relevant decisions, "
                    "warnings, knowledge, history, constraints, contradictions, and unknowns. "
                    "Call this before planning or editing."
                ),
                inputSchema={
                    "type": "object",
                    "required": ["task"],
                    "properties": {
                        "task": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 20000,
                            "description": "The concrete task the LLM is about to perform",
                        },
                        "repo_id": {"type": "string"},
                        "environment": RUNTIME_SCOPE_SCHEMA,
                        "task_type": RUNTIME_SCOPE_SCHEMA,
                        "files": {"type": "array", "items": {"type": "string"}},
                        "symbols": {"type": "array", "items": {"type": "string"}},
                        "intent_id": {"type": "string"},
                        "constraints": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "token_budget": {
                            "type": "integer",
                            "minimum": 128,
                            "maximum": 100000,
                            "default": 2000,
                        },
                        "min_confidence": {
                            "type": "number",
                            "minimum": 0,
                            "maximum": 1,
                            "default": 0,
                        },
                        "previous_fingerprint": {
                            "type": "string",
                            "description": "Prior brief fingerprint for an unchanged delta",
                        },
                        "format": {
                            "type": "string",
                            "enum": ["text", "json"],
                            "default": "text",
                        },
                    },
                },
            ),
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
                        "query": {
                            "type": "string",
                            "description": "Optional task query for compact ranked context",
                        },
                        "repo_id": {"type": "string"},
                        "environment": RUNTIME_SCOPE_SCHEMA,
                        "task_type": RUNTIME_SCOPE_SCHEMA,
                        "token_budget": {
                            "type": "integer",
                            "minimum": 64,
                            "maximum": 100000,
                            "default": 2000,
                        },
                        "previous_fingerprint": {
                            "type": "string",
                            "description": (
                                "Prior fingerprint; unchanged context returns no payload"
                            ),
                        },
                        "files": {"type": "array", "items": {"type": "string"}},
                        "symbols": {"type": "array", "items": {"type": "string"}},
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
                        "layers": {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "enum": ["raw", "episodic", "semantic", "intent"],
                            },
                            "description": (
                                "Optional list of memory layers to search "
                                "(default: all except raw)"
                            ),
                        },
                        "repo_id": {
                            "type": "string",
                            "description": (
                                "Filter by repository/project ID for isolation (optional)"
                            ),
                        },
                        "environment": RUNTIME_SCOPE_SCHEMA,
                        "task_type": RUNTIME_SCOPE_SCHEMA,
                        "log_utility": {
                            "type": "boolean",
                            "default": False,
                            "description": "Log surfaced results as recall utility feedback",
                        },
                        "task_id": {
                            "type": "string",
                            "description": "Optional task ID for surfaced feedback",
                        },
                        "task": {
                            "type": "string",
                            "description": "Current task text for intent-aware ranking",
                        },
                        "files": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Files in the current work context",
                        },
                        "session_id": {
                            "type": "string",
                            "description": "Session ID for ranking context",
                        },
                        "constraints": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Current constraints for ranking context",
                        },
                        "dependencies": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Relevant repo/package dependencies",
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
            Tool(
                name="memory_trace",
                description="Trace query-relevant memories through evidence-backed relationships.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Recall query"},
                        "repo_id": {"type": "string", "description": "Repository/project ID"},
                        "depth": {"type": "integer", "default": 2},
                        "token_budget": {"type": "integer", "default": 2000},
                        "limit": {"type": "integer", "default": 5},
                        "environment": RUNTIME_SCOPE_SCHEMA,
                        "task_type": RUNTIME_SCOPE_SCHEMA,
                        "as_of": {"type": "string", "format": "date-time"},
                    },
                    "required": ["query"],
                },
            ),
            Tool(
                name="memory_neighbors",
                description="Get a compact evidence-backed neighborhood for a memory.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "memory_id": {"type": "string", "description": "Memory ID"},
                        "repo_id": {"type": "string", "description": "Repository/project ID"},
                        "relationship_filter": {"type": "string"},
                        "depth": {"type": "integer", "default": 1},
                        "token_budget": {"type": "integer", "default": 2000},
                        "limit": {"type": "integer", "default": 25},
                        "environment": RUNTIME_SCOPE_SCHEMA,
                        "task_type": RUNTIME_SCOPE_SCHEMA,
                        "as_of": {"type": "string", "format": "date-time"},
                    },
                    "required": ["memory_id"],
                },
            ),
            Tool(
                name="memory_path",
                description="Find the shortest evidence-backed path between two memories.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "source_id": {"type": "string", "description": "Source memory ID"},
                        "target_id": {"type": "string", "description": "Target memory ID"},
                        "repo_id": {"type": "string", "description": "Repository/project ID"},
                        "max_hops": {"type": "integer", "default": 4},
                        "token_budget": {"type": "integer", "default": 2000},
                        "environment": RUNTIME_SCOPE_SCHEMA,
                        "task_type": RUNTIME_SCOPE_SCHEMA,
                        "as_of": {"type": "string", "format": "date-time"},
                    },
                    "required": ["source_id", "target_id"],
                },
            ),
            Tool(
                name="memory_why_relevant",
                description="Explain why a memory is relevant to a query.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Recall query"},
                        "memory_id": {"type": "string", "description": "Memory ID"},
                        "repo_id": {"type": "string", "description": "Repository/project ID"},
                        "depth": {"type": "integer", "default": 2},
                        "token_budget": {"type": "integer", "default": 2000},
                        "limit": {"type": "integer", "default": 5},
                        "environment": RUNTIME_SCOPE_SCHEMA,
                        "task_type": RUNTIME_SCOPE_SCHEMA,
                        "as_of": {"type": "string", "format": "date-time"},
                    },
                    "required": ["query", "memory_id"],
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
                            "description": (
                                "Repository/project ID; may use the configured repository "
                                "but never global scope"
                            ),
                        },
                        "environment": RUNTIME_SCOPE_SCHEMA,
                        "task_type": RUNTIME_SCOPE_SCHEMA,
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
                        "repo_id": {
                            "type": "string",
                            "description": (
                                "Repository/project ID; may use the configured repository "
                                "but never global scope"
                            ),
                        },
                        "environment": RUNTIME_SCOPE_SCHEMA,
                        "task_type": RUNTIME_SCOPE_SCHEMA,
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
                                "preference",
                                "procedure",
                                "prohibition",
                                "hypothesis",
                                "negative",
                            ],
                            "default": "fact",
                            "description": "Type of knowledge",
                        },
                        "authority_attestation": {
                            "type": "string",
                            "description": (
                                "Opaque signed prohibition envelope for central verification"
                            ),
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
                        "repo_id": {"type": "string", "description": "Repository/project ID"},
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
                description=(
                    "Record an assisted task-completion outcome. "
                    "Intent status remains externally owned and unchanged."
                ),
                inputSchema={"type": "object", "properties": {}},
            ),
            Tool(
                name="memory_update_intent",
                description=(
                    "Update goal content by ID. Status inputs are recorded as "
                    "non-authoritative outcome history and do not change status."
                ),
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
            Tool(
                name="memory_feedback_log",
                description="Log recall utility feedback for surfaced, used, or dismissed memory.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "memory_id": {"type": "string", "description": "Single memory ID"},
                        "memory_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Memory IDs to mark",
                        },
                        "event_type": {
                            "type": "string",
                            "enum": [
                                "surfaced",
                                "used",
                                "dismissed",
                                "task_linked",
                                "outcome_linked",
                                "task-linked",
                                "outcome-linked",
                            ],
                        },
                        "repo_id": {"type": "string", "description": "Repository/project ID"},
                        "query": {"type": "string", "description": "Query to hash, not store"},
                        "task_id": {"type": "string", "description": "Optional task ID"},
                        "outcome": {"type": "string", "description": "Optional outcome ID/label"},
                    },
                    "required": ["event_type"],
                },
            ),
            Tool(
                name="memory_feedback_inspect",
                description="Inspect aggregate recall utility feedback signals.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "memory_id": {"type": "string", "description": "Filter by memory ID"},
                        "repo_id": {"type": "string", "description": "Repository/project ID"},
                        "event_type": {"type": "string", "description": "Filter by event type"},
                        "limit": {"type": "integer", "default": 50},
                    },
                },
            ),
            Tool(
                name="memory_feedback_reset",
                description="Reset recall utility feedback signals matching filters.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "memory_id": {"type": "string", "description": "Filter by memory ID"},
                        "repo_id": {"type": "string", "description": "Repository/project ID"},
                        "event_type": {"type": "string", "description": "Filter by event type"},
                        "confirm": {
                            "type": "boolean",
                            "default": False,
                            "description": "Must be true to reset",
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
                description=(
                    "Record assisted completion outcomes for active goals without "
                    "changing their status. Requires confirm=true."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "confirm": {
                            "type": "boolean",
                            "default": False,
                            "description": "Must be true to record outcomes for active goals",
                        },
                    },
                },
            ),
            Tool(
                name="memory_model_task",
                description=(
                    "Run an LLM-required memory task. Uses the connected MCP client's model "
                    "through sampling when supported, otherwise the configured server provider."
                ),
                inputSchema={
                    "type": "object",
                    "required": ["task", "prompt"],
                    "properties": {
                        "task": {
                            "type": "string",
                            "enum": [
                                "extraction",
                                "reconciliation",
                                "consolidation",
                                "merge_suggestion",
                                "reflection",
                                "intent_verification",
                                "reranking",
                                "answer",
                            ],
                        },
                        "prompt": {"type": "string", "minLength": 1, "maxLength": 20000},
                        "system_prompt": {"type": "string", "maxLength": 4000},
                        "max_tokens": {
                            "type": "integer",
                            "minimum": 64,
                            "maximum": 4000,
                            "default": 800,
                        },
                    },
                },
            ),
        ]
        profile = _resolve_tool_profile()
        tools = _filter_tools_by_profile(all_tools, profile)
        logger.info(
            "Advertising %d/%d MCP tools (profile=%s)", len(tools), len(all_tools), profile
        )
        return tools

    # =========================================================================
    # Tool Handlers
    # =========================================================================

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        """Handle tool calls."""
        # P10-US-07: the profile is enforced at DISPATCH, not only in
        # advertisement. Before this check the filter ran in list_tools while
        # call_tool dispatched by name with no profile check — a restriction
        # that only rearranged the menu. A tool outside the active profile is
        # now a structured refusal, never a silent execution.
        profile = _resolve_tool_profile()
        if profile != "full":
            allowed = _profile_tool_names(profile, frozenset())
            if name not in allowed:
                refusal = {
                    "success": False,
                    "error": "tool_not_in_profile",
                    "profile": profile,
                    "tool": name,
                    "reason": (
                        f"The active MCP profile '{profile}' does not permit {name}. "
                        "Set VISP_MEMORY_MCP_PROFILE=full to expose the full surface."
                    ),
                }
                return [TextContent(type="text", text=json.dumps(refusal))]
        try:
            if name == "memory_model_task":
                from visp_memory.core.model_router import ModelRouter

                task = str(arguments.get("task", ""))
                prompt = str(arguments.get("prompt", ""))
                system_prompt = str(arguments.get("system_prompt", ""))
                max_tokens = max(64, min(int(arguments.get("max_tokens", 800)), 4000))
                if not prompt:
                    raise ValueError("prompt is required")
                session = server.request_context.session
                client_params = session.client_params
                supports_sampling = bool(
                    client_params
                    and client_params.capabilities
                    and client_params.capabilities.sampling
                )
                if supports_sampling:
                    sampled = await session.create_message(
                        [
                            SamplingMessage(
                                role="user",
                                content=TextContent(type="text", text=prompt),
                            )
                        ],
                        max_tokens=max_tokens,
                        system_prompt=system_prompt or None,
                    )
                    content = sampled.content
                    if isinstance(content, list):
                        text_parts = [
                            item.text for item in content if isinstance(item, TextContent)
                        ]
                        output = "\n".join(text_parts)
                    elif isinstance(content, TextContent):
                        output = content.text
                    else:
                        output = str(content)
                    result = {
                        "task": task,
                        "provider": "mcp-sampling",
                        "model": sampled.model,
                        "text": output,
                    }
                else:
                    result = ModelRouter(memory.config.llm).complete(
                        task,
                        prompt,
                        system_prompt=system_prompt or None,
                    )
                return [TextContent(type="text", text=json.dumps(result, indent=2))]
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

            from visp_memory.recall.proactive import ProactiveRecall

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
                description="Use visp-memory effectively during a Codex coding session",
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
                                "For this Codex session, use visp-memory as durable project "
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
    if args.get("query"):
        from visp_memory.core.context_compiler import ContextCompiler

        compiled = ContextCompiler(memory._storage).compile(
            str(args["query"]),
            repo_id=args.get("repo_id") or memory.config.repo_id,
            token_budget=int(args.get("token_budget", 2000)),
            files=args.get("files") or [],
            symbols=args.get("symbols") or [],
            previous_fingerprint=args.get("previous_fingerprint"),
            environment=args.get("environment"),
            task_type=args.get("task_type"),
        )
        if fmt == "json":
            return json.dumps(compiled, indent=2, default=str)
        if compiled["unchanged"]:
            return f"Context unchanged. Fingerprint: {compiled['fingerprint']}"
        if compiled["abstained"]:
            return f"No reliable context: {compiled['abstention_reason']}"
        return (
            f"Context fingerprint: {compiled['fingerprint']}\n"
            f"Token count: {compiled['token_count']}/{compiled['token_budget']}\n\n"
            f"{compiled['context']}"
        )
    include_history = args.get("include_history", True)
    ctx = memory.context(
        format=fmt,
        include_history=include_history,
        repo_id=args.get("repo_id"),
        environment=args.get("environment"),
        task_type=args.get("task_type"),
    )
    if fmt == "json":
        return json.dumps(ctx, indent=2, default=str)
    return ctx


def _handle_task_brief(args: dict[str, Any], memory: Memory) -> str:
    """Prepare the primary recall-before-work artifact."""
    from visp_memory.core.task_brief import TaskMemoryBriefCompiler

    task = str(args.get("task") or "").strip()
    if not task:
        return "Error: task is required."
    brief = TaskMemoryBriefCompiler(memory._storage).prepare(
        task,
        repo_id=args.get("repo_id") or memory.config.repo_id,
        token_budget=int(args.get("token_budget", 2000)),
        files=args.get("files") or [],
        symbols=args.get("symbols") or [],
        intent_id=args.get("intent_id"),
        constraints=args.get("constraints") or [],
        previous_fingerprint=args.get("previous_fingerprint"),
        min_confidence=float(args.get("min_confidence", 0.0)),
        environment=args.get("environment"),
        task_type=args.get("task_type"),
    )
    if args.get("format", "text") == "json":
        return json.dumps(brief, indent=2, default=str)
    if brief["unchanged"]:
        return f"Task brief unchanged. Fingerprint: {brief['fingerprint']}"
    return brief["context"]


def _handle_search(name: str, args: dict[str, Any], memory: Memory) -> str:
    """Handle search tools."""
    if name == "memory_recall":
        layers = args.get("layers")
        if layers is not None:
            if not isinstance(layers, list):
                return "Error: 'layers' must be an array of layer names."
            invalid = [layer for layer in layers if layer not in VALID_LAYERS]
            if invalid:
                return (
                    f"Error: invalid layer(s) {invalid}. "
                    f"Valid layers are: {', '.join(sorted(VALID_LAYERS))}."
                )
        results = memory.recall(
            query=args["query"],
            layers=layers,
            limit=args.get("limit", 10),
            repo_id=args.get("repo_id"),
            log_utility=args.get("log_utility", False),
            task_id=args.get("task_id"),
            task=args.get("task"),
            files=args.get("files"),
            session_id=args.get("session_id"),
            constraints=args.get("constraints"),
            dependencies=args.get("dependencies"),
            environment=args.get("environment"),
            task_type=args.get("task_type"),
        )
        if not results:
            return "No memories found matching query."

        output = [f"Found {len(results)} memories:\n"]
        for r in results:
            sim = f" (similarity: {r.get('similarity', 0):.2f})" if r.get("similarity") else ""
            output.append(
                f"- [{r['id']}] [{r['layer']}/{r.get('category', 'unknown')}]{sim}: "
                f"{r['content']}"
            )
            output.extend(_format_ranking_factor_lines(r))
            output.extend(_format_related_evidence_lines(r.get("id"), memory))
        return "\n".join(output)

    elif name == "memory_remember":
        layer = args.get("layer")
        if layer is not None and layer not in VALID_LAYERS:
            return (
                f"Error: invalid layer '{layer}'. "
                f"Valid layers are: {', '.join(sorted(VALID_LAYERS))}."
            )
        memories = memory._storage.list_memories(
            repo_id=args.get("repo_id") or memory.config.repo_id,
            layer=layer,
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
                output.extend(_format_related_evidence_lines(w.get("id"), memory))

        if relevant["knowledge"]:
            output.append("\n**Relevant Knowledge:**")
            for k in relevant["knowledge"][:5]:
                output.append(f"  - {k['content']}")
                output.extend(_format_related_evidence_lines(k.get("id"), memory))

        if relevant["history"]:
            output.append("\n**Related History:**")
            for h in relevant["history"][:3]:
                output.append(f"  - {h['content']}")
                output.extend(_format_related_evidence_lines(h.get("id"), memory))

        return "\n".join(output) if output else "No relevant memories found."

    return f"Unknown search tool: {name}"


def _handle_proactive(name: str, args: dict[str, Any], memory: Memory) -> str:
    """Handle proactive recall tools."""
    if name == "memory_file_context":
        from visp_memory.recall.proactive import ProactiveRecall

        recall = ProactiveRecall(memory)

        context = recall.on_file_open(
            file_path=args["file_path"], include_related=args.get("include_related", True)
        )

        formatted = recall.format_injection(context, format="markdown")
        return formatted if formatted.strip() else "No context found for this file."

    elif name == "memory_find_error":
        from visp_memory.recall.proactive import ProactiveRecall

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
        from visp_memory.recall.proactive import ProactiveRecall

        recall = ProactiveRecall(memory)

        context = recall.on_directory(
            dir_path=args["dir_path"], recursive=args.get("recursive", False)
        )

        formatted = recall.format_injection(context, format="markdown")
        return formatted if formatted.strip() else "No context found for this directory."

    return f"Unknown proactive tool: {name}"


def _format_evidence_value(evidence: dict[str, Any] | None) -> str | None:
    if not evidence:
        return None

    parts = []
    confidence = evidence.get("confidence")
    if confidence:
        parts.append(f"confidence={confidence}")

    score = evidence.get("confidence_score")
    if isinstance(score, (int, float)):
        parts.append(f"score={score:.2f}")

    source = evidence.get("source")
    if source:
        parts.append(f"source={source}")

    reason = evidence.get("reason")
    if reason:
        parts.append(f"reason={reason}")

    return "; ".join(parts) if parts else None


def _format_related_evidence_lines(memory_id: str | None, memory: Memory) -> list[str]:
    if not memory_id:
        return []

    try:
        related = memory._storage.get_related_memories(memory_id)
    except Exception as exc:  # pragma: no cover - defensive MCP output guard
        logger.debug("Could not load related memory evidence for %s: %s", memory_id, exc)
        return []

    lines = []
    for item in related[:3]:
        evidence_text = _format_evidence_value(
            item.get("relationship_evidence") or item.get("evidence")
        )
        if not evidence_text:
            continue
        relationship = item.get("relationship", "related")
        content = str(item.get("content") or item.get("id") or "related memory")
        snippet = content.replace("\n", " ")[:80]
        lines.append(f"    Relationship evidence ({relationship} -> {snippet}): {evidence_text}")
    return lines


def _format_ranking_factor_lines(memory: dict[str, Any]) -> list[str]:
    explanation = memory.get("ranking_explanation") or []
    if not explanation:
        return []
    return [f"    Ranking factors: {'; '.join(explanation)}"]


def _format_relevant_memory(
    relevant: dict[str, list[dict[str, Any]]], memory: Memory | None = None
) -> str:
    output = []
    if relevant.get("warnings"):
        output.append("**Warnings:**")
        for w in relevant["warnings"]:
            output.append(f"  - {w['content']}")
            if memory:
                output.extend(_format_related_evidence_lines(w.get("id"), memory))

    if relevant.get("knowledge"):
        output.append("\n**Relevant Knowledge:**")
        for k in relevant["knowledge"][:8]:
            output.append(f"  - {k['content']}")
            if memory:
                output.extend(_format_related_evidence_lines(k.get("id"), memory))

    if relevant.get("history"):
        output.append("\n**Related History:**")
        for h in relevant["history"][:5]:
            output.append(f"  - {h['content']}")
            if memory:
                output.extend(_format_related_evidence_lines(h.get("id"), memory))

    return "\n".join(output) if output else "No relevant memories found."


def _format_graph_recall(result: dict[str, Any]) -> str:
    lines = [f"# Graph Recall: {result.get('mode', 'unknown')}"]
    if result.get("explanation"):
        lines.append(result["explanation"])
    if result.get("query"):
        lines.append(f"Query: {result['query']}")

    lines.append("\n## Memories")
    for node in result.get("nodes", []):
        score = node.get("relevance_score", 0.0)
        factors = node.get("relevance_factors", {})
        distance = factors.get("distance", 0)
        lines.append(
            f"- {node['id']} [{node.get('layer', 'unknown')}] "
            f"score={score:.2f} distance={distance}: {node.get('content', '')}"
        )

    lines.append("\n## Relationships")
    if result.get("edges"):
        for edge in result["edges"]:
            evidence = edge.get("evidence") or {}
            reason = edge.get("reason") or evidence.get("reason") or "No reason recorded."
            confidence = evidence.get("confidence", "unknown")
            lines.append(
                f"- {edge['source_id']} -> {edge['target_id']} "
                f"({edge.get('relationship', 'related')}): {reason} "
                f"[confidence={confidence}, score={edge.get('relevance_score', 0.0):.2f}]"
            )
    else:
        lines.append("- No relationship evidence returned.")

    if result.get("omitted"):
        lines.append("\n## Omitted Context")
        for item in result["omitted"]:
            lines.append(f"- {item.get('count', 1)} {item.get('type')}: {item.get('reason')}")

    return "\n".join(lines)


def _handle_workflow(name: str, args: dict[str, Any], memory: Memory) -> str:
    """Handle Codex-style recall-before-work and record-after-work tools."""
    if name == "memory_session_start":
        task = args.get("task")
        files = args.get("files")
        repo_id = require_repo_id(args.get("repo_id") or memory.config.repo_id)

        lines = ["# Visp Memory Session Context"]
        if task:
            intent_id = memory.working_on(task=task, files=files, repo_id=repo_id)
            lines.append(f"\nCurrent task recorded (ID: {intent_id}): {task}")

        context = memory.context(
            format="text",
            include_history=True,
            repo_id=repo_id,
            environment=args.get("environment"),
            task_type=args.get("task_type"),
        )
        lines.append(f"\n{context}")

        if task or files:
            relevant = memory.relevant_for(
                task=task,
                files=files,
                repo_id=repo_id,
                environment=args.get("environment"),
                task_type=args.get("task_type"),
            )
            lines.append("\n## Task-Relevant Memory")
            lines.append(_format_relevant_memory(relevant, memory))

        return "\n".join(lines)

    if name == "memory_before_change":
        task = args.get("task")
        files = args.get("files")
        relevant = memory.relevant_for(
            task=task,
            files=files,
            repo_id=args.get("repo_id"),
            environment=args.get("environment"),
            task_type=args.get("task_type"),
        )
        header = "# Before Changing Code"
        if files:
            header += f"\nFiles: {', '.join(files)}"
        if task:
            header += f"\nTask: {task}"
        return f"{header}\n\n{_format_relevant_memory(relevant, memory)}"

    if name == "memory_after_work":
        repo_id = args.get("repo_id")
        summary_id = memory.record(
            event=args["summary"],
            category=args.get("category", "note"),
            importance=0.7,
            repo_id=repo_id,
            _write_channel=WriteChannel.MCP,
        )
        recorded = [f"Recorded summary (ID: {summary_id})"]

        for decision in args.get("decisions") or []:
            decision_id = memory.decision(
                what=decision,
                why="Recorded from Codex end-of-work summary.",
                repo_id=repo_id,
                _write_channel=WriteChannel.MCP,
            )
            recorded.append(f"Recorded decision (ID: {decision_id}): {decision}")

        for bug in args.get("bugs_fixed") or []:
            bug_id = memory.record(
                event=bug,
                category="bug_fixed",
                importance=0.8,
                repo_id=repo_id,
                _write_channel=WriteChannel.MCP,
            )
            recorded.append(f"Recorded bug fix (ID: {bug_id}): {bug}")

        for item in args.get("warnings") or []:
            warning_id = memory.warn(
                area=item["area"],
                warning=item["warning"],
                severity=0.8,
                repo_id=repo_id,
                _write_channel=WriteChannel.MCP,
            )
            recorded.append(f"Recorded warning (ID: {warning_id}): {item['area']}")

        return "\n".join(recorded)

    return f"Unknown workflow tool: {name}"


def _handle_graph_recall(name: str, args: dict[str, Any], memory: Memory) -> str:
    """Handle graph-shaped recall tools."""
    if name == "memory_trace":
        return _format_graph_recall(
            memory.graph_trace(
                query=args["query"],
                repo_id=args.get("repo_id"),
                depth=args.get("depth", 2),
                token_budget=args.get("token_budget", 2000),
                limit=args.get("limit", 5),
                environment=args.get("environment"),
                task_type=args.get("task_type"),
                as_of=args.get("as_of"),
            )
        )

    if name == "memory_neighbors":
        return _format_graph_recall(
            memory.graph_neighbors(
                memory_id=args["memory_id"],
                relationship_filter=args.get("relationship_filter"),
                repo_id=args.get("repo_id"),
                depth=args.get("depth", 1),
                token_budget=args.get("token_budget", 2000),
                limit=args.get("limit", 25),
                environment=args.get("environment"),
                task_type=args.get("task_type"),
                as_of=args.get("as_of"),
            )
        )

    if name == "memory_path":
        return _format_graph_recall(
            memory.graph_path(
                source_id=args["source_id"],
                target_id=args["target_id"],
                repo_id=args.get("repo_id"),
                max_hops=args.get("max_hops", 4),
                token_budget=args.get("token_budget", 2000),
                environment=args.get("environment"),
                task_type=args.get("task_type"),
                as_of=args.get("as_of"),
            )
        )

    if name == "memory_why_relevant":
        return _format_graph_recall(
            memory.graph_why_relevant(
                query=args["query"],
                memory_id=args["memory_id"],
                repo_id=args.get("repo_id"),
                depth=args.get("depth", 2),
                token_budget=args.get("token_budget", 2000),
                limit=args.get("limit", 5),
                environment=args.get("environment"),
                task_type=args.get("task_type"),
                as_of=args.get("as_of"),
            )
        )

    return f"Unknown graph recall tool: {name}"


#: MCP tools that persist something and therefore need a repository scope.
#: Listed rather than inferred, so adding a write tool is a deliberate decision
#: about whether it belongs here — the alternative is a new surface silently
#: reopening the bug, which is exactly how the CLI ended up with nine of them.
_WRITE_TOOLS = frozenset(
    {
        "memory_record",
        "memory_decision",
        "memory_learn",
        "memory_warn",
        "memory_issue",
        "memory_goal",
        "memory_working_on",
        "memory_done",
        "memory_update_intent",
        "memory_close_intent",
    }
)


def _refuse_unscoped_write(name: str, args: dict[str, Any], memory: Memory) -> str | None:
    """Refuse a write with no resolvable scope. None means the call may proceed.

    Returns the refusal as ordinary tool text: the caller is a model, and a
    sentence it can act on beats an exception it has to interpret.
    """
    if name not in _WRITE_TOOLS:
        return None
    scope = args.get("repo_id") or memory.config.repo_id
    if isinstance(scope, str) and scope.strip() and scope.strip() != UNSCOPED_REPO_ID:
        return None
    return (
        f"Refused: {name} needs a repository scope, and none is configured. Anything written "
        "without one is quarantined and no recall will return it. Run `visp-memory init` in the "
        "project, or pass repo_id."
    )


def _handle_recording(name: str, args: dict[str, Any], memory: Memory) -> str:
    """Handle recording tools."""
    if name == "memory_record":
        mem_id = memory.record(
            event=args["event"],
            category=args.get("category", "note"),
            importance=args.get("importance", 0.5),
            repo_id=args.get("repo_id"),
            _write_channel=WriteChannel.MCP,
        )
        return f"Recorded event (ID: {mem_id}): {args['event']}"

    elif name == "memory_decision":
        mem_id = memory.decision(
            what=args["what"],
            why=args["why"],
            alternatives=args.get("alternatives"),
            repo_id=args.get("repo_id"),
            _write_channel=WriteChannel.MCP,
        )
        return f"Decision recorded (ID: {mem_id}): {args['what']}"

    return f"Unknown recording tool: {name}"


def _handle_knowledge(name: str, args: dict[str, Any], memory: Memory) -> str:
    """Handle knowledge tools."""
    if name == "memory_learn":
        if "epistemic_status" in args:
            raise ValueError(
                "initial epistemic status is assigned by the memory service"
            )
        mem_id = memory.learn(
            knowledge=args["knowledge"],
            category=args.get("category", "fact"),
            importance=args.get("importance", 0.6),
            repo_id=args.get("repo_id"),
            detect_conflicts=args.get("detect_conflicts", False),
            authority_attestation=args.get("authority_attestation"),
            _write_channel=WriteChannel.MCP,
        )
        return f"Knowledge established (ID: {mem_id}): {args['knowledge']}"

    elif name == "memory_warn":
        mem_id = memory.warn(
            area=args["area"],
            warning=args["warning"],
            severity=args.get("severity", 0.7),
            repo_id=args.get("repo_id"),
            _write_channel=WriteChannel.MCP,
        )
        return f"Warning added for {args['area']}: {args['warning']}"

    elif name == "memory_issue":
        mem_id = memory.semantic.known_issue(
            issue=args["issue"],
            workaround=args.get("workaround"),
            priority=args.get("priority", 0.5),
            repo_id=args.get("repo_id"),
            _write_channel=WriteChannel.MCP,
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
        recorded = memory.done(actor_id="mcp-client", channel=WriteChannel.MCP)
        return f"Recorded {recorded} task outcome(s); intent status unchanged"

    elif name == "memory_update_intent":
        status = args.get("status")
        if status is not None and status not in VALID_INTENT_STATUSES:
            return (
                f"Error: invalid status '{status}'. "
                f"Valid statuses are: {', '.join(sorted(VALID_INTENT_STATUSES))}."
            )
        update_data = {
            key: args[key]
            for key in ("description", "priority", "status")
            if key in args and args[key] is not None
        }
        if not update_data:
            return "No intent fields provided to update."
        updated = memory.intent.update(
            args["intent_id"],
            **update_data,
            actor_id="mcp-client",
            channel=WriteChannel.MCP,
        )
        if not updated:
            return f"Intent not found: {args['intent_id']}"
        if status is not None:
            return (
                f"Intent updated; {status} outcome recorded, status unchanged: "
                f"{args['intent_id']}"
            )
        return f"Intent updated: {args['intent_id']}"

    elif name == "memory_close_intent":
        closed = memory.intent.close(
            args["intent_id"],
            actor_id="mcp-client",
            channel=WriteChannel.MCP,
        )
        if not closed:
            return f"Intent not found: {args['intent_id']}"
        return f"Close outcome recorded, status unchanged: {args['intent_id']}"

    return f"Unknown intent tool: {name}"


def _parse_memory_datetime(value: Any) -> datetime:
    """Parse storage timestamps into aware UTC so age math matches utc_now()."""
    return parse_utc(value) or utc_now()


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
    now = utc_now()
    previews = []
    for item in memories:
        current = float(item.get("importance", 0.5) or 0.0)
        accessed = _parse_memory_datetime(item.get("accessed_at") or item.get("created_at"))
        age_days = max(0.0, (now - accessed).total_seconds() / 86400)
        projected = projected_importance(
            importance=current,
            age_days=age_days,
            halflife_days=halflife_days,
            access_count=item.get("access_count", 0),
            min_importance=min_importance,
        )
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


def _handle_feedback(name: str, args: dict[str, Any], memory: Memory) -> str:
    """Handle recall utility feedback tools."""
    if name == "memory_feedback_log":
        memory_ids = list(args.get("memory_ids") or [])
        if args.get("memory_id"):
            memory_ids.append(args["memory_id"])
        memory_ids = list(dict.fromkeys(memory_ids))
        if not memory_ids:
            return "No memory_id or memory_ids provided."

        event_ids = []
        for memory_id in memory_ids:
            event_ids.append(
                memory.record_utility_feedback(
                    memory_id=memory_id,
                    event_type=args["event_type"],
                    repo_id=args.get("repo_id"),
                    query=args.get("query"),
                    task_id=args.get("task_id"),
                    outcome=args.get("outcome"),
                    metadata={"source": "mcp"},
                )
            )
        return f"Recorded {len(event_ids)} feedback events: {', '.join(event_ids)}"

    if name == "memory_feedback_inspect":
        return json.dumps(
            memory.inspect_utility_signals(
                memory_id=args.get("memory_id"),
                repo_id=args.get("repo_id"),
                event_type=args.get("event_type"),
                limit=args.get("limit", 50),
            ),
            indent=2,
            default=str,
        )

    if name == "memory_feedback_reset":
        if not args.get("confirm", False):
            return "Set confirm=true to reset recall utility feedback."
        deleted = memory.reset_utility_signals(
            memory_id=args.get("memory_id"),
            repo_id=args.get("repo_id"),
            event_type=args.get("event_type"),
        )
        return f"Deleted {deleted} feedback events."

    return f"Unknown feedback tool: {name}"


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
        if not args.get("confirm", False):
            return "Set confirm=true to record outcomes for all active goals."
        recorded = memory.intent.clear_all(
            actor_id="mcp-client",
            channel=WriteChannel.MCP,
        )
        return f"Recorded {recorded} goal outcomes; intent status unchanged."

    return f"Unknown maintenance tool: {name}"


async def handle_tool(name: str, args: dict[str, Any], memory: Memory) -> str:
    """Route tool calls to specialized handlers."""

    # Context
    if name == "memory_prepare_task":
        return _handle_task_brief(args, memory)

    if name == "memory_context":
        return _handle_context(args, memory)

    # Search
    if name in ["memory_recall", "memory_remember", "memory_relevant"]:
        return _handle_search(name, args, memory)

    if name in ["memory_trace", "memory_neighbors", "memory_path", "memory_why_relevant"]:
        return _handle_graph_recall(name, args, memory)

    # Proactive
    if name in ["memory_file_context", "memory_find_error", "memory_directory_context"]:
        return _handle_proactive(name, args, memory)

    # Codex workflow
    if name in ["memory_session_start", "memory_before_change", "memory_after_work"]:
        return _handle_workflow(name, args, memory)

    # Every tool below this point WRITES, so each one is checked once here.
    #
    # Without this, `memory_record` in a project that never ran `init` returned
    # "Recorded event (ID: ...)" for a row the engine had quarantined, and the
    # matching `memory_recall` raised. An agent has even less chance than a
    # human of noticing: it gets an id back and moves on, and the memory it
    # believes it saved is one nothing will ever return. The refusal names the
    # repair because the model is the one that has to act on it.
    refusal = _refuse_unscoped_write(name, args, memory)
    if refusal is not None:
        return refusal

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

    # Recall utility feedback
    if name in ["memory_feedback_log", "memory_feedback_inspect", "memory_feedback_reset"]:
        return _handle_feedback(name, args, memory)

    # Maintenance
    if name in ["memory_compress", "memory_decay", "memory_decay_preview", "memory_clear_goals"]:
        return _handle_maintenance(name, args, memory)

    return f"Unknown tool: {name}"


async def run_server():
    """Run the MCP server."""
    if not MCP_AVAILABLE:
        print("Error: MCP package not installed.")
        print("Install with: pip install visp-memory[mcp]")
        return

    server = create_mcp_server()

    async with stdio_server() as (read_stream, write_stream):
        logger.info("Visp Memory MCP server starting...")
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main():
    """Entry point for MCP server."""
    import asyncio

    asyncio.run(run_server())


if __name__ == "__main__":
    main()
