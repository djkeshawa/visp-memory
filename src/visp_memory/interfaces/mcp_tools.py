"""The advertised MCP tool surface: definitions and profiles.

Split out of ``mcp.py`` so the transport/handler module is not dominated by
860 lines of static schema. Everything here is data and pure functions: no
Memory instance, no I/O, no server state — which is also why both transports
(stdio and stateless HTTP) can share it verbatim.

The tool texts are pinned by ``tests/interfaces/test_mcp_profile_footprint.py``:
that test serialises exactly what a client receives and asserts the ``core``
profile's documented byte saving, so edits here are measured, not free.
"""

import logging
import os

try:
    from mcp.types import Tool

    MCP_TYPES_AVAILABLE = True
except ImportError:
    MCP_TYPES_AVAILABLE = False

logger = logging.getLogger("visp-memory-mcp")

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


def build_tool_definitions() -> list["Tool"]:
    """The full advertised tool surface, before profile filtering.

    Built per call rather than held as a module constant: `Tool` is only
    importable with the mcp extra installed, and a list_tools request is the
    only consumer.
    """
    if not MCP_TYPES_AVAILABLE:
        raise ImportError("MCP package not installed. Install with: pip install visp-memory[mcp]")
    return [
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
