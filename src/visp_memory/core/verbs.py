"""The memory verbs that generated assistant instructions name.

One catalogue, rendered by every adapter template.

The Claude Code, Aider and Codex templates each spelled this list out by hand,
and the intent verbs were absent from all three: an assistant reading its own
generated instructions had no way to learn that ``goal`` / ``focus`` /
``working`` / ``done`` existed. A list written in four places is a list that is
wrong in three of them, so the templates now import it from here.

Nothing in this module confers authority. An intent is *direction*: memory
records what the work is aimed at. It never grants scope, certifies evidence,
marks work complete, or declares anything ready.
"""

from dataclasses import dataclass
from typing import Optional, Sequence


@dataclass(frozen=True)
class MemoryVerb:
    """One CLI verb an assistant may be told about.

    Attributes:
        name: The CLI sub-command.
        example: A copy-pasteable invocation.
        summary: One line, imperative, describing what it records.
        mcp_tool: Equivalent MCP tool, or ``None`` when the verb is CLI-only.
    """

    name: str
    example: str
    summary: str
    mcp_tool: Optional[str] = None


CAPTURE_VERBS: tuple[MemoryVerb, ...] = (
    MemoryVerb(
        name="record",
        example='visp-memory record "what happened" -c <category>',
        summary="Record something that happened.",
        mcp_tool="memory_record",
    ),
    MemoryVerb(
        name="decision",
        example='visp-memory decision "what" "why" --alt "alternative"',
        summary="Record a design decision and the reasoning behind it.",
        mcp_tool="memory_decision",
    ),
    MemoryVerb(
        name="bug",
        example='visp-memory bug "issue" --fix "solution"',
        summary="Record a bug and how it was fixed.",
    ),
    MemoryVerb(
        name="learn",
        example='visp-memory learn "knowledge" -c <category>',
        summary="Establish durable knowledge about the project.",
        mcp_tool="memory_learn",
    ),
    MemoryVerb(
        name="warn",
        example='visp-memory warn "area" "what to watch out for"',
        summary="Flag a fragile area so it surfaces before it is touched again.",
        mcp_tool="memory_warn",
    ),
)

INTENT_VERBS: tuple[MemoryVerb, ...] = (
    MemoryVerb(
        name="goal",
        example='visp-memory goal "the objective" --priority 2 --constraint "..."',
        summary="State the objective the current work is aimed at.",
        mcp_tool="memory_goal",
    ),
    MemoryVerb(
        name="focus",
        example='visp-memory focus "area" --avoid "unrelated refactors"',
        summary="Narrow to one area and name what to stay away from (CLI only).",
    ),
    MemoryVerb(
        name="working",
        example='visp-memory working "current task" --file <path>',
        summary="Say which task is in hand right now, and the files it touches.",
        mcp_tool="memory_working_on",
    ),
    MemoryVerb(
        name="done",
        example="visp-memory done",
        summary="Record a completion outcome for the current task in memory.",
        mcp_tool="memory_done",
    ),
)

MEMORY_VERBS: tuple[MemoryVerb, ...] = CAPTURE_VERBS + INTENT_VERBS

# The single sentence every template carries next to the intent verbs. Memory is
# non-authoritative (AGENTS.md rule 9): recording direction is not being granted
# it, and `done` records an outcome rather than producing one.
INTENT_NON_AUTHORITATIVE_NOTE = (
    "Intents are direction, not permission. Memory records them; it does not grant "
    "scope, certify evidence, or decide that work is finished. `done` records a "
    "completion outcome in memory — the task is finished when the project's own "
    "checks and review say so."
)

# What the lifecycle is for, in one line, for templates that introduce it.
INTENT_LIFECYCLE_HEADLINE = (
    "Keep the current direction in memory so the next session — and the injected "
    "context block — knows what this work is aimed at."
)


def render_command_block(verbs: Sequence[MemoryVerb]) -> str:
    """Render verbs as a fenced shell block of example invocations."""
    lines = "\n".join(verb.example for verb in verbs)
    return f"```bash\n{lines}\n```"


def render_bullets(verbs: Sequence[MemoryVerb]) -> str:
    """Render verbs as markdown bullets: example, then what it records."""
    return "\n".join(f"- `{verb.example}` — {verb.summary}" for verb in verbs)


def render_inline_commands(verbs: Sequence[MemoryVerb]) -> str:
    """Render verb names as a comma-separated inline list of CLI commands."""
    return ", ".join(f"`visp-memory {verb.name}`" for verb in verbs)


def render_inline_mcp_tools(verbs: Sequence[MemoryVerb]) -> str:
    """Render the MCP tool names for verbs that have one, inline.

    Names the CLI-only verbs explicitly. The two lists are not positionally
    aligned - `focus` has no tool - and an assistant reading four verbs against
    three tools would otherwise pair them off in order and call the wrong one.
    """
    tools = ", ".join(f"`{verb.mcp_tool}`" for verb in verbs if verb.mcp_tool)
    cli_only = [verb.name for verb in verbs if not verb.mcp_tool]
    if not cli_only:
        return tools
    names = ", ".join(f"`{name}`" for name in cli_only)
    verb_word = "is" if len(cli_only) == 1 else "are"
    return f"{tools}; {names} {verb_word} CLI-only"
