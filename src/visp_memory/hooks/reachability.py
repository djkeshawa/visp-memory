"""Can an agent working in this repository find its way into memory?

On 2026-08-15 an agent was told to use four Visp products on one greenfield task
and to record and recall its decisions. Three of the four left evidence on disk.
Memory left none. It had not failed, and it had not been called: the string
``visp-memory`` did not occur anywhere in the project tree the agent worked in —
not in its 87-line ``AGENTS.md``, not in any of the twelve prompt files, not in
the fourteen recorded runs. The agent was obeying an instruction file that told
it to run the next command it was handed, and no chain of next commands ever
named memory. There was no way in, so no way in was found.

That is a discoverability defect and it belongs to this package, because this
package is the one that knows whether an entry point exists. ``visp-memory init``
writes a config file and a data directory, both of which are invisible to a
coding agent — ``visp-memory.yaml`` is even filtered out as noise by other tools'
diff summaries. Nothing in an initialised project tells an agent that memory is
here or how to call it. ``visp-memory hooks install <tool>`` writes exactly that
instruction, and nothing pointed at it.

So this module answers one question about a directory: *if an agent read the
instruction files in this project, would it learn how to call memory?* The answer
is reported by ``doctor`` and at the end of ``init``, so an absent entry point is
a visible state rather than a silence.

Deliberately conservative. Naming the package in prose is not an entry point, and
is reported as ``mentioned`` rather than ``reachable`` — an agent that reads
"this project uses visp-memory" still does not know a single command to type. Only
an invocable command form counts as reachable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from visp_memory.capture.instructions import INSTRUCTION_FILE_PATTERNS

#: Subcommands that make a mention actionable. This is deliberately a subset of
#: the CLI's full command list — every name here is asserted against the real
#: Typer app by ``tests/test_agent_reachability.py``, so the set cannot drift into
#: naming a command that no longer exists. Adding a command to the CLI does not
#: require adding it here; these are the ones an instruction file would realistically
#: hand to an agent.
ENTRY_COMMANDS = (
    "audit",
    "brief",
    "bug",
    "context",
    "convention",
    "decision",
    "doctor",
    "focus",
    "goal",
    "hooks",
    "ingest-instructions",
    "init",
    "issue",
    "learn",
    "preview",
    "recall",
    "record",
    "remember",
    "serve",
    "stats",
    "warn",
    "working",
)

#: What to run when nothing is reachable.
REMEDIATION = "visp-memory hooks install <tool>   (see: visp-memory hooks list)"

# ``visp-memory`` as a bare word. ``visp-memory.yaml``, ``.visp-memory/`` and
# ``visp-memory-dashboard`` do not match: a config path is not something an agent
# can invoke, and reporting one as an entry point would let the defect hide.
_MENTION_RE = re.compile(r"(?<![\w.-])visp-memory(?![\w.-])")
_MCP_RE = re.compile(r"(?<![\w.-])visp-memory-mcp(?![\w-])")
_TOKEN_RE = re.compile(r"[^\s`'\"]+")

ABSENT = "absent"
MENTIONED = "mentioned"
REACHABLE = "reachable"


@dataclass
class ReachabilityReport:
    """What an agent reading this project's instruction files would learn."""

    status: str = ABSENT
    instruction_files: list[str] = field(default_factory=list)
    entry_points: list[str] = field(default_factory=list)
    mentions_only: list[str] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)

    @property
    def reachable(self) -> bool:
        return self.status == REACHABLE

    def headline(self) -> str:
        """One line, phrased so an absent entry point cannot be read as fine."""
        if self.status == REACHABLE:
            return f"reachable - named in {', '.join(self.entry_points)}"
        if self.status == MENTIONED:
            return (
                f"not reachable - {', '.join(self.mentions_only)} names visp-memory "
                "but gives no command to run"
            )
        if self.instruction_files:
            return f"not reachable - {', '.join(self.instruction_files)} never mentions visp-memory"
        return "not reachable - this project has no agent instruction file"

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reachable": self.reachable,
            "instruction_files": self.instruction_files,
            "entry_points": self.entry_points,
            "mentions_only": self.mentions_only,
            "unreadable": self.unreadable,
            "remediation": None if self.reachable else REMEDIATION,
        }


def find_instruction_files(
    root: Path, patterns: Sequence[str] = INSTRUCTION_FILE_PATTERNS
) -> list[Path]:
    """Every well-known agent instruction file present under ``root``.

    The same patterns ``ingest-instructions`` reads, so the two commands agree on
    what counts as an instruction file.
    """
    found: list[Path] = []
    for pattern in patterns:
        if any(ch in pattern for ch in "*?["):
            found.extend(sorted(p for p in root.glob(pattern) if p.is_file()))
        else:
            candidate = root / pattern
            if candidate.is_file():
                found.append(candidate)
    # Preserve pattern order, drop duplicates from overlapping globs.
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in found:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(path)
    return unique


def _names_a_command(text: str, at: int) -> bool:
    """Does a runnable subcommand follow the executable at offset ``at``?

    Scans the rest of the line only. A command on the next line is a different
    instruction, and running the two together is not what the file said.
    """
    line_end = text.find("\n", at)
    tail = text[at:] if line_end == -1 else text[at:line_end]
    tokens = _TOKEN_RE.findall(tail)[1:]  # drop the executable itself

    # Step over global flags and their values only. The first token that is
    # neither must be the subcommand, or this is prose rather than a command:
    # "visp-memory for durable context" has to stay unreachable, and "context"
    # being a real command name is exactly why the scan cannot simply look ahead.
    expecting_flag_value = False
    for token in tokens:
        if expecting_flag_value:
            expecting_flag_value = False
            continue
        if token.startswith("-"):
            expecting_flag_value = "=" not in token
            continue
        return token.strip(".,;:)]") in ENTRY_COMMANDS
    return False


def classify_text(text: str) -> str:
    """``REACHABLE``, ``MENTIONED`` or ``ABSENT`` for one instruction file's text."""
    if _MCP_RE.search(text):
        return REACHABLE

    mentioned = False
    for match in _MENTION_RE.finditer(text):
        mentioned = True
        if _names_a_command(text, match.start()):
            return REACHABLE
    return MENTIONED if mentioned else ABSENT


def check_reachability(
    root: Path, patterns: Iterable[str] = INSTRUCTION_FILE_PATTERNS
) -> ReachabilityReport:
    """Report whether an agent reading ``root``'s instruction files could call memory.

    Never raises on a project's own files: an instruction file that cannot be
    decoded is listed under ``unreadable`` rather than aborting the check, because
    this runs inside ``doctor`` and ``init`` and must not turn a diagnostic into a
    crash.
    """
    root = Path(root)
    report = ReachabilityReport()

    for path in find_instruction_files(root, tuple(patterns)):
        try:
            rel = str(path.relative_to(root))
        except ValueError:  # pragma: no cover - defensive, patterns are relative
            rel = str(path)
        report.instruction_files.append(rel)

        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            report.unreadable.append(rel)
            continue

        status = classify_text(text)
        if status == REACHABLE:
            report.entry_points.append(rel)
        elif status == MENTIONED:
            report.mentions_only.append(rel)

    if report.entry_points:
        report.status = REACHABLE
    elif report.mentions_only:
        report.status = MENTIONED
    else:
        report.status = ABSENT
    return report
