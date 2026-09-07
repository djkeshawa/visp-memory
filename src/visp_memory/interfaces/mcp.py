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

For a shared deployment (one server, many clients, no per-client process), run
the stateless streamable-HTTP transport instead: ``visp-memory-mcp-http``
(see :mod:`visp_memory.interfaces.mcp_http`). Both transports serve the same
server built by :func:`create_mcp_server`.
"""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Iterator

import anyio

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import (
        GetPromptResult,
        Prompt,
        PromptArgument,
        PromptMessage,
        Resource,
        ResourceTemplate,
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
from visp_memory.core.embedding_status import (
    DEFAULT_SCORE_LABEL,
    LEXICAL_RECALL_BANNER,
    LEXICAL_SCORE_LABEL,
)
from visp_memory.core.model_router import ModelUnavailableError
from visp_memory.core.ranking import projected_importance
from visp_memory.core.trust import WriteChannel

# The advertised tool surface (definitions, profiles, and their env resolution)
# lives in mcp_tools; re-exported here because this module is the public MCP
# namespace tests and integrators import from.
from visp_memory.interfaces.mcp_tools import (  # noqa: F401,E402
    CORE_TOOL_NAMES,
    HTTP_HIDDEN_TOOL_NAMES,
    HTTP_REPO_TOOL_NAMES,
    HTTP_REPO_WRITE_TOOL_NAMES,
    READONLY_TOOL_NAMES,
    RUNTIME_SCOPE_SCHEMA,
    VALID_MCP_PROFILES,
    _filter_tools_by_profile,
    _http_scoped_tool,
    _profile_tool_names,
    _resolve_tool_profile,
    build_tool_definitions,
)

if TYPE_CHECKING:
    from visp_memory.server.auth import UserContext

logger = logging.getLogger("visp-memory-mcp")

# MCP schema enums are advisory-only in this SDK: a client can send any value
# regardless of the declared enum. Validation therefore has to happen server-side
# in the handlers below. These allow-lists are the canonical sets.
VALID_LAYERS = frozenset({"raw", "episodic", "semantic", "intent"})
VALID_INTENT_STATUSES = frozenset({"active", "completed", "closed"})


@dataclass(frozen=True)
class MCPRequestContext:
    """Transport and principal metadata for one MCP request."""

    transport: str = "stdio"
    principal: UserContext | None = None
    request_id: str | None = None
    require_explicit_scope: bool = False


_request_context: ContextVar[MCPRequestContext] = ContextVar(
    "visp_memory_mcp_request_context", default=MCPRequestContext()
)


def current_mcp_request_context() -> MCPRequestContext:
    """Return the current transport context, defaulting to the stdio contract."""
    return _request_context.get()


@contextmanager
def bind_mcp_request_context(context: MCPRequestContext) -> Iterator[None]:
    """Bind request metadata across the MCP SDK's child tasks and worker thread."""
    token = _request_context.set(context)
    try:
        yield
    finally:
        _request_context.reset(token)


class MCPAuthorizationError(RuntimeError):
    """A safe, actionable authorization refusal for an MCP client."""


def _mcp_error_payload(code: str, message: str, **extra: Any) -> dict[str, Any]:
    """Return the stable, correlated shape used for client-visible MCP errors."""
    payload: dict[str, Any] = {
        "success": False,
        "code": code,
        "request_id": current_mcp_request_context().request_id,
        "message": message,
    }
    payload.update(extra)
    return payload


def _mcp_error_text(code: str, message: str, **extra: Any) -> str:
    return json.dumps(_mcp_error_payload(code, message, **extra))


def _requires_explicit_scope() -> bool:
    """Treat HTTP as scoped even for callers that build the legacy context shape."""
    context = current_mcp_request_context()
    return context.require_explicit_scope or context.transport == "http"


def _http_resource_templates() -> list[ResourceTemplate]:
    """Describe only resources that carry an explicit repository scope."""
    return [
        ResourceTemplate(
            uriTemplate="memory://repo/{repo_id}/context",
            name="Repository Memory Context",
            description="Full memory context for one repository",
            mimeType="text/markdown",
        ),
        ResourceTemplate(
            uriTemplate="memory://repo/{repo_id}/warnings",
            name="Repository Warnings",
            description="Warnings for one repository",
            mimeType="text/plain",
        ),
        ResourceTemplate(
            uriTemplate="memory://repo/{repo_id}/goals",
            name="Repository Goals",
            description="Active goals for one repository",
            mimeType="text/plain",
        ),
        ResourceTemplate(
            uriTemplate="memory://repo/{repo_id}/conventions",
            name="Repository Conventions",
            description="Conventions for one repository",
            mimeType="text/plain",
        ),
        ResourceTemplate(
            uriTemplate="memory://repo/{repo_id}/stats",
            name="Repository Memory Statistics",
            description="Memory statistics for one repository",
            mimeType="application/json",
        ),
        ResourceTemplate(
            uriTemplate="memory://repo/{repo_id}/file/{path}",
            name="Repository File Context",
            description="Context for a file in one repository",
            mimeType="text/markdown",
        ),
        ResourceTemplate(
            uriTemplate="memory://repo/{repo_id}/session",
            name="Repository Session",
            description="Current session context for one repository",
            mimeType="text/markdown",
        ),
    ]


def _read_http_resource(uri_str: str, memory: Memory) -> str:
    """Read a repository resource after validating its explicit URI scope."""
    from urllib.parse import unquote

    parts = uri_str.split("/", 4)
    if len(parts) < 5 or parts[:3] != ["memory:", "", "repo"]:
        raise ValueError(
            _mcp_error_text(
                "authorization_denied",
                "An explicit repository-scoped resource URI is required over HTTP.",
            )
        )

    repo_id = unquote(parts[3])
    resource = unquote(parts[4])
    try:
        _http_authorize_repo(repo_id, memory, write=False)
    except MCPAuthorizationError as error:
        logger.warning(
            "MCP resource authorization denied request_id=%s reason=%s",
            current_mcp_request_context().request_id,
            error,
        )
        raise ValueError(
            _mcp_error_text(
                "authorization_denied",
                "The requested resource is not available to this token.",
            )
        ) from error

    if resource == "context":
        return memory.context(format="text", repo_id=repo_id)
    if resource == "warnings":
        warnings = memory.semantic.get_warnings(repo_id=repo_id)
        return "No warnings." if not warnings else "\n".join(
            f"- {warning['content']}" for warning in warnings
        )
    if resource == "goals":
        intents = memory.intent.get_active(repo_id=repo_id)
        if not intents:
            return "No active goals."
        priority_labels = {0: "LOW", 1: "NORMAL", 2: "HIGH", 3: "CRITICAL"}
        return "\n".join(
            f"[{priority_labels.get(intent.get('priority', 1), str(intent.get('priority')))}] "
            f"{intent['description']}"
            for intent in intents
        )
    if resource == "conventions":
        conventions = memory.semantic.get_conventions(repo_id=repo_id)
        return "No conventions established." if not conventions else "\n".join(
            f"- {convention['content']}" for convention in conventions
        )
    if resource == "stats":
        return json.dumps(memory.stats(repo_id=repo_id), indent=2)
    if resource == "session":
        summary = memory.intent.summarize(repo_id=repo_id)
        lines = ["# Current Session\n"]
        if summary.get("current_task"):
            lines.append(f"**Current Task:** {summary['current_task']['description']}\n")
        if summary.get("focus"):
            lines.append(f"**Focus:** {summary['focus']['description']}\n")
        if summary.get("constraints"):
            lines.append("\n**Constraints:**")
            lines.extend(f"- {constraint}" for constraint in summary["constraints"])
            lines.append("")
        recent = memory.episodic.recent(limit=5, repo_id=repo_id)
        if recent:
            lines.append("\n**Recent Activity:**")
            lines.extend(
                f"- [{item['category']}] {item['content'][:100]}" for item in recent
            )
        return "\n".join(lines)
    if resource.startswith("file/"):
        from visp_memory.recall.proactive import ProactiveRecall

        file_path = resource[len("file/") :]
        recall = ProactiveRecall(memory, repo_id=repo_id)
        context = recall.on_file_open(file_path)
        formatted = recall.format_injection(context, format="markdown")
        return f"# Context for {file_path}\n\n{formatted}"

    raise ValueError(
        _mcp_error_text("resource_not_found", "The requested memory resource is not available.")
    )


def create_mcp_server() -> "Server":
    """Create and configure the MCP server."""
    if not MCP_AVAILABLE:
        raise ImportError("MCP package not installed. Install with: pip install visp-memory[mcp]")

    server = Server("visp-memory")
    memory = Memory()
    # The HTTP wrapper needs the same Memory config to select the credential
    # database. Keep this private attachment out of the public MCP API.
    server._visp_memory = memory

    # =========================================================================
    # Tool Definitions
    # =========================================================================

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        """List available memory tools, scoped to the active profile."""
        all_tools = build_tool_definitions()
        profile = _resolve_tool_profile()
        tools = _filter_tools_by_profile(all_tools, profile)
        if _requires_explicit_scope():
            tools = [tool for tool in tools if tool.name not in HTTP_HIDDEN_TOOL_NAMES]
            tools = [
                _http_scoped_tool(tool) if tool.name in HTTP_REPO_TOOL_NAMES else tool
                for tool in tools
            ]
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
                message = (
                    f"The active MCP profile '{profile}' does not permit {name}. "
                    "Set VISP_MEMORY_MCP_PROFILE=full to expose the full surface."
                )
                logger.warning(
                    "MCP tool denied by profile request_id=%s tool=%s profile=%s",
                    current_mcp_request_context().request_id,
                    name,
                    profile,
                )
                return [
                    TextContent(
                        type="text",
                        text=_mcp_error_text(
                            "tool_not_in_profile",
                            message,
                            error="tool_not_in_profile",
                            profile=profile,
                            tool=name,
                            reason=message,
                        ),
                    )
                ]
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
                    # ModelRouter is deliberately synchronous. A stateless HTTP
                    # request must not pin the event loop while a provider waits on
                    # network I/O or retries.
                    result = await anyio.to_thread.run_sync(
                        lambda: ModelRouter(memory.config.llm).complete(
                            task,
                            prompt,
                            system_prompt=system_prompt or None,
                        )
                    )
                return [TextContent(type="text", text=json.dumps(result, indent=2))]
            result = await handle_tool(name, arguments, memory)
            return [TextContent(type="text", text=result)]
        except MCPAuthorizationError as error:
            logger.warning(
                "MCP authorization denied request_id=%s tool=%s reason=%s",
                current_mcp_request_context().request_id,
                name,
                error,
            )
            return [
                TextContent(
                    type="text",
                    text=_mcp_error_text(
                        "authorization_denied",
                        str(error),
                        error="authorization_denied",
                    ),
                )
            ]
        except ModelUnavailableError:
            # Keep the operator repair, but never echo provider-generated text.
            logger.warning(
                "MCP model unavailable request_id=%s tool=%s",
                current_mcp_request_context().request_id,
                name,
            )
            return [
                TextContent(
                    type="text",
                    text=_mcp_error_text(
                        "model_unavailable",
                        "No server-side LLM provider is configured.",
                    ),
                )
            ]
        except Exception:
            # Exception text can contain provider responses, filesystem paths, SQL,
            # or credentials. Keep it in server logs only.
            logger.exception(
                "Error handling MCP tool request_id=%s tool=%s",
                current_mcp_request_context().request_id,
                name,
            )
            return [
                TextContent(
                    type="text",
                    text=_mcp_error_text(
                        "request_failed",
                        "The server could not complete the request; check server logs.",
                    ),
                )
            ]

    # =========================================================================
    # Resources
    # =========================================================================

    @server.list_resource_templates()
    async def list_resource_templates() -> list[ResourceTemplate]:
        """Advertise repository-scoped resources only to the HTTP transport."""
        if not _requires_explicit_scope():
            return []
        return _http_resource_templates()

    @server.list_resources()
    async def list_resources() -> list[Resource]:
        """List available memory resources."""
        if _requires_explicit_scope():
            return []
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
        if _requires_explicit_scope():
            return _read_http_resource(uri_str, memory)
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
        if current_mcp_request_context().transport == "http":
            return []
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
        if current_mcp_request_context().transport == "http":
            raise ValueError(
                "Prompts are unavailable over stateless HTTP; use a scoped MCP tool."
            )
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

        context_repo_id = args.get("repo_id") or memory.config.repo_id
        compiled = ContextCompiler(
            memory._storage, code_graph=memory.code_graph(context_repo_id)
        ).compile(
            str(args["query"]),
            repo_id=context_repo_id,
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
    brief_repo_id = args.get("repo_id") or memory.config.repo_id
    brief = TaskMemoryBriefCompiler(
        memory._storage, code_graph=memory.code_graph(brief_repo_id)
    ).prepare(
        task,
        repo_id=brief_repo_id,
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
        # An agent reads this number and quotes it onward, so it says what it is:
        # two rounds of battle-ground notes reported lexical overlap as semantic
        # similarity because this line called it "similarity" either way. The
        # label is therefore unconditional. The banner is not — appending
        # "install embeddings" to every response of a deployment that pinned the
        # provider to `none` on purpose is noise an agent pays for on every call.
        lexical = memory.recall_scores_are_lexical is True
        remediation = memory.lexical_recall_remediation
        banner = f"{LEXICAL_RECALL_BANNER} {remediation}" if remediation else None
        if not results:
            if banner:
                return f"No memories found matching query.\n\n{banner}"
            return "No memories found matching query."

        score_label = LEXICAL_SCORE_LABEL if lexical else DEFAULT_SCORE_LABEL
        output = [f"Found {len(results)} memories:\n"]
        for r in results:
            sim = f" ({score_label}: {r.get('similarity', 0):.2f})" if r.get("similarity") else ""
            output.append(
                f"- [{r['id']}] [{r['layer']}/{r.get('category', 'unknown')}]{sim}: "
                f"{r['content']}"
            )
            output.extend(_format_ranking_factor_lines(r))
            output.extend(_format_related_evidence_lines(r.get("id"), memory))
        if banner:
            output.append(f"\n{banner}")
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
        relevant = memory.relevant_for(
            task=args.get("task"), files=args.get("files"), repo_id=args.get("repo_id")
        )
        return _format_relevant_memory(relevant, memory)

    return f"Unknown search tool: {name}"


def _handle_proactive(name: str, args: dict[str, Any], memory: Memory) -> str:
    """Handle proactive recall tools."""
    if name == "memory_file_context":
        from visp_memory.recall.proactive import ProactiveRecall

        recall = ProactiveRecall(memory, repo_id=args.get("repo_id"))

        context = recall.on_file_open(
            file_path=args["file_path"], include_related=args.get("include_related", True)
        )

        formatted = recall.format_injection(context, format="markdown")
        return formatted if formatted.strip() else "No context found for this file."

    elif name == "memory_find_error":
        from visp_memory.recall.proactive import ProactiveRecall

        recall = ProactiveRecall(memory, repo_id=args.get("repo_id"))

        similar = recall.on_error(
            error_message=args["error_message"],
            error_type=args.get("error_type"),
            file_path=args.get("file_path"),
            limit=args.get("limit", 5),
        )

        if not similar:
            return "No similar errors found."

        # Same storage field as memory_recall, so the same rule applies: under noop
        # this is `text_similarity` and must not be handed to an agent as similarity.
        error_score_label = (
            LEXICAL_SCORE_LABEL.title()
            if memory.recall_scores_are_lexical is True
            else "Similarity"
        )
        output = [f"Found {len(similar)} similar errors:\n"]
        for i, err in enumerate(similar, 1):
            similarity = err.get("similarity", 0)
            content = err["content"][:200]
            output.append(f"{i}. [{error_score_label}: {similarity:.2f}] {content}")

            if "fix" in content.lower() or "solution" in content.lower():
                output.append("   ✓ Contains fix/solution")

        return "\n".join(output)

    elif name == "memory_directory_context":
        from visp_memory.recall.proactive import ProactiveRecall

        recall = ProactiveRecall(memory, repo_id=args.get("repo_id"))

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
        "memory_after_work",
        "memory_feedback_log",
        "memory_feedback_reset",
    }
)


def _http_refusal(message: str) -> MCPAuthorizationError:
    """Build a refusal whose text contains no user-controlled or backend data."""
    return MCPAuthorizationError(message)


def _http_authorize_repo(repo_id: Any, memory: Memory, *, write: bool) -> str:
    """Authorize one explicitly named repository for the current HTTP principal."""
    context = current_mcp_request_context()
    principal = context.principal
    if not _requires_explicit_scope():
        return str(repo_id)
    if principal is None:
        raise _http_refusal("An authenticated HTTP principal is required.")
    if not isinstance(repo_id, str) or not repo_id.strip():
        raise _http_refusal("An explicit repo_id is required for stateless HTTP requests.")
    required_scope = "memory:write" if write else "memory:read"
    if not principal.allows(required_scope):
        raise _http_refusal("The personal access token lacks the required memory scope.")
    try:
        from visp_memory.server.authorization import (
            require_repo_scope_access,
            require_repo_writable,
        )

        if write:
            require_repo_writable(memory._storage, repo_id, principal)
        else:
            require_repo_scope_access(memory._storage, repo_id, principal)
    except Exception as error:
        # Authorization helpers are shared with FastAPI and use HTTPException.
        # Do not pass their detail through: a future helper may include a path,
        # repository metadata, or another value supplied by the caller.
        status_code = getattr(error, "status_code", None)
        if status_code == 409:
            raise _http_refusal("The requested repository is archived.") from error
        if status_code == 400 and "repo_id" in str(getattr(error, "detail", "")):
            raise _http_refusal(
                "An explicit repo_id is required for stateless HTTP requests."
            ) from error
        raise _http_refusal("The requested repository is not available to this token.") from error
    return repo_id


def _http_validate_record_ids(
    args: dict[str, Any],
    memory: Memory,
    repo_id: str,
    principal: UserContext | None = None,
) -> None:
    """Prevent ID-addressed reads and mutations from escaping the chosen repo."""
    from visp_memory.server.authorization import can_access_scoped_record

    principal = principal or current_mcp_request_context().principal
    if principal is None:
        raise _http_refusal("An authenticated HTTP principal is required.")

    memory_ids: list[Any] = []
    for key in ("memory_id", "source_id", "target_id"):
        if args.get(key):
            memory_ids.append(args[key])
    memory_ids.extend(args.get("memory_ids") or [])
    for memory_id in memory_ids:
        record = memory._storage.get_memory(str(memory_id))
        if (
            not record
            or record.get("repo_id") != repo_id
            or not can_access_scoped_record(
                memory._storage, record, principal, scope_field="metadata"
            )
        ):
            raise _http_refusal("The requested memory is not available in this repository scope.")

    intent_id = args.get("intent_id")
    if intent_id:
        intents = memory._storage.get_active_intents(repo_id=None, status="all")
        intent = next((item for item in intents if item.get("id") == intent_id), None)
        if (
            not intent
            or intent.get("repo_id") != repo_id
            or not can_access_scoped_record(
                memory._storage, intent, principal, scope_field="context"
            )
        ):
            raise _http_refusal("The requested intent is not available in this repository scope.")


def _http_preflight(name: str, args: dict[str, Any], memory: Memory) -> None:
    """Apply the common HTTP policy before any tool can touch durable state."""
    context = current_mcp_request_context()
    if not _requires_explicit_scope():
        return
    if name in HTTP_HIDDEN_TOOL_NAMES:
        raise _http_refusal("Global maintenance tools are disabled over stateless HTTP.")
    if name not in HTTP_REPO_TOOL_NAMES:
        return
    principal = context.principal
    repo_id = _http_authorize_repo(
        args.get("repo_id"),
        memory,
        write=name in HTTP_REPO_WRITE_TOOL_NAMES
        or (name == "memory_recall" and bool(args.get("log_utility"))),
    )
    if principal is None:  # _http_authorize_repo normally raises first.
        raise _http_refusal("An authenticated HTTP principal is required.")
    _http_validate_record_ids(args, memory, repo_id, principal)


def _refuse_unscoped_write(name: str, args: dict[str, Any], memory: Memory) -> str | None:
    """Refuse a write with no resolvable scope, and pin the resolved one into args.

    Returns the refusal as ordinary tool text — the caller is a model, and a
    sentence it can act on beats an exception it has to interpret. None means
    the call may proceed.

    THE INJECTION IS NOT A CONVENIENCE. Checking that a scope resolves is not
    the same as the write using it, and the gap between those two is a live
    defect: `memory_issue` calls `memory.semantic.known_issue` — a LAYER method
    — while its siblings call `memory.learn`/`memory.warn`, which are FACADE
    methods. The facade applies `config.repo_id`; the layer does not. So with no
    explicit repo_id the guard resolved a perfectly good scope from config, said
    yes, and the handler then wrote `repo_id=None`, which `store_memory` turned
    into the quarantine sentinel and stamped Provenance.UNKNOWN.

    Every known issue recorded through MCP was quarantined that way, in
    correctly initialized projects, with the tool reporting success.

    Writing the resolved scope back into `args` fixes it for every tool at once
    rather than for the one that was noticed, which is the same argument that
    put this guard at the dispatch point instead of in nine handlers.
    """
    if name not in _WRITE_TOOLS:
        return None
    scope = args.get("repo_id") or memory.config.repo_id
    if isinstance(scope, str) and scope.strip() and scope.strip() != UNSCOPED_REPO_ID:
        args["repo_id"] = scope.strip()
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
        recorded = memory.done(
            repo_id=args.get("repo_id"), actor_id="mcp-client", channel=WriteChannel.MCP
        )
        return f"Recorded {recorded} task outcome(s); intent status unchanged"

    elif name == "memory_update_intent":
        if args.get("workflow_report") is not None:
            if any(args.get(key) is not None for key in ("description", "priority", "status")):
                raise ValueError("Send a workflow report separately from ordinary intent edits")
            principal = current_mcp_request_context().principal
            may_manage_workflow = principal is None
            if principal is not None:
                from visp_memory.server.authorization import has_admin_privileges

                may_manage_workflow = has_admin_privileges(principal)
            if not may_manage_workflow:
                intent = next((item for item in memory._storage.get_active_intents(status="all")
                               if item["id"] == args["intent_id"]), None)
                if (
                    not intent
                    or (intent.get("context") or {}).get("author_id") != principal.user_id
                ):
                    raise _http_refusal("Only the intent owner can connect a workflow reporter")
            result = memory._storage.report_intent_workflow(
                args["intent_id"], args["workflow_report"],
                actor_id=principal.user_id if principal else "local-workflow", channel="mcp",
            )
            return json.dumps(result)
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
        stats = memory.stats(repo_id=args.get("repo_id"))
        return json.dumps(stats, indent=2)

    elif name == "memory_list_warnings":
        warnings = memory.semantic.get_warnings(repo_id=args.get("repo_id"))
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
    """Route a tool call to its handler on a worker thread.

    Every handler below is synchronous — SQLite, embedding, and graph work.
    Under the stdio transport (one client, sequential requests) running them on
    the event loop was harmless; the stateless HTTP transport serves concurrent
    requests from one process, and a blocking handler there would serialize
    every caller behind the slowest recall. The offload keeps the public
    signature (awaited by both transports and by tests) unchanged.
    """
    return await anyio.to_thread.run_sync(_dispatch_tool, name, args, memory)


def _dispatch_tool(name: str, args: dict[str, Any], memory: Memory) -> str:
    """Route tool calls to specialized handlers."""

    _http_preflight(name, args, memory)

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

    # Configured here, not at import time: this module is imported by tests and
    # by the HTTP entrypoint, and a library import must not rewire the host
    # process's root logger. StreamHandler writes to stderr, so protocol frames
    # on stdout stay clean.
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_server())


if __name__ == "__main__":
    main()
