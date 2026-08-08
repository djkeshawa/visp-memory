"""
Command Line Interface for Visp Memory.

Usage:
    visp-memory init                    # Initialize in current directory
    visp-memory record "Fixed bug"      # Record an event
    visp-memory decision "..." "..."    # Record a decision
    visp-memory learn "..."             # Establish knowledge
    visp-memory warn "area" "warning"   # Add a warning
    visp-memory goal "..."              # Set a goal
    visp-memory working "task"          # Set current task
    visp-memory done                    # Record task outcome; status is unchanged
    visp-memory recall "query"          # Search memories
    visp-memory context                 # Get full context
    visp-memory brief "task"            # Prepare a cited task brief
    visp-memory stats                   # Show statistics
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional

import typer
from typer.core import TyperGroup
from rich.console import Console, Group
from rich.layout import Layout
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from visp_memory import Memory, MemoryConfig, __version__
from visp_memory.config import load_config
from visp_memory.core.authority import (
    build_prohibition_claim,
    sign_prohibition_attestation,
)
from visp_memory.core.beliefs import BeliefType
from visp_memory.core.clock import parse_utc, utc_now
from visp_memory.core.eligibility import UNSCOPED_REPO_ID
from visp_memory.core.ranking import projected_importance
from visp_memory.core.reporting import MemoryIntelligenceReporter
from visp_memory.core.trust import WriteChannel

console = Console()


class _RefusalBoundary(TyperGroup):
    """Turn a foreseeable input error into a refusal instead of a stack dump.

    `visp-memory recall` used to answer a user who had simply not run `init`
    with a full Rich traceback: source lines, frames, and every local variable
    in scope — including the entire Memory object. That is not a debugging aid
    for the person on the other end, it is noise that buries the one sentence
    they needed, and it prints internal state to whatever the terminal is being
    logged to.

    A ValueError reaching this point is a refusal the domain has already
    phrased, so it is shown as one. Set VISP_MEMORY_DEBUG=1 to re-raise and get
    the traceback back when the cause really is a bug in here rather than a
    problem with the input.
    """

    def invoke(self, ctx):
        try:
            return super().invoke(ctx)
        except ValueError as exc:
            if os.environ.get("VISP_MEMORY_DEBUG"):
                raise
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc


app = typer.Typer(
    name="visp-memory",
    help="Human-inspired memory system for LLMs",
    no_args_is_help=True,
    cls=_RefusalBoundary,
)

# Global memory instance (lazy loaded)
_memory: Optional[Memory] = None


def version_callback(value: bool):
    """Print CLI version and exit."""
    if value:
        console.print(f"visp-memory {__version__}")
        raise typer.Exit()


@app.callback()
def cli_root(
    version: bool = typer.Option(
        False,
        "--version",
        callback=version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
):
    """Human-inspired memory system for LLMs."""


def get_memory() -> Memory:
    """Get or create memory instance."""
    global _memory
    if _memory is None:
        _memory = Memory()
    return _memory


def _intents_matching(memory: Memory, query: str, repo: str = None) -> int:
    """How many active intents contain the query, for a recall that found nothing.

    Deliberately a plain substring count rather than a semantic search: this is
    a signpost, not a second retrieval path, and it must never be the reason a
    recall is slow or an unreviewed intent looks like a ranked result.
    """
    try:
        intents = memory._storage.get_active_intents(repo_id=_repo_scope(memory, repo))
    except Exception:
        # A signpost may never be the reason a command fails.
        return 0

    terms = [term for term in query.lower().split() if len(term) > 2]
    if not terms:
        return 0
    return sum(
        1
        for intent in intents
        if any(term in str(intent.get("description", "")).lower() for term in terms)
    )


def ensure_gitignored(project_root: Path, data_dir: Path) -> list[str]:
    """Add this tool's own store to .gitignore. Returns what was added.

    `init` writes a SQLite database and binary vector indexes — data_level0.bin,
    link_lists.bin, chroma.sqlite3 — into the working tree, roughly a megabyte
    for a handful of memories, and never ignored them. That is not a tidiness
    concern: during a dogfooding session the file volume stopped the project
    owner's IDE from loading the repository, and they added the entries by hand
    mid-run to get working again.

    A tool that writes a data directory into someone's project is responsible
    for keeping it out of their history. `visp-memory.yaml` is deliberately NOT
    ignored: the config is meant to be committed and shared, unlike the store.

    Idempotent, and it never rewrites an existing line — it appends only what
    is genuinely absent, so a project with its own conventions keeps them.
    """
    gitignore = project_root / ".gitignore"
    if not (project_root / ".git").exists():
        return []

    try:
        existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    except OSError:
        return []

    present = {line.strip().rstrip("/") for line in existing.splitlines()}
    wanted = [f"{data_dir.parts[0]}/"] if data_dir.parts else []

    missing = [entry for entry in wanted if entry.rstrip("/") not in present]
    if not missing:
        return []

    separator = "" if existing.endswith("\n") or existing == "" else "\n"
    addition = f"{separator}\n# Visp Memory's local store (database and vector indexes)\n"
    addition += "".join(f"{entry}\n" for entry in missing)
    try:
        gitignore.write_text(existing + addition, encoding="utf-8")
    except OSError:
        return []
    return missing


def _repo_scope(memory: Memory, repo: str = None) -> str:
    """Resolve a CLI repository override against the configured default.

    Deliberately still returns None when nothing resolves. The `contract`
    subcommands call this and must answer with a JSON envelope rather than
    exit, because Hyper parses their stdout against a strict schema — raising
    here would replace the envelope with a traceback on the channel another
    product is reading.
    """
    return repo or memory.config.repo_id


def _require_repo_scope(memory: Memory, repo: str = None, *, writing: bool = False) -> str:
    """Resolve the scope for a human CLI command, or refuse with the repair.

    The domain refuses an unscoped operation with "repo_id is required", which
    is accurate and useless: it names the field, not the fix. A person who has
    not run `init` cannot act on it. This adds the two things they can do.

    Reads and writes get different first lines because they have different
    consequences — saying "this memory would be saved" on a `recall` would be
    describing something the command was never going to do.
    """
    scope = _repo_scope(memory, repo)
    if isinstance(scope, str) and scope.strip() and scope.strip() != UNSCOPED_REPO_ID:
        return scope.strip()

    # Naming the reserved bucket is a different mistake from naming nothing, and
    # telling someone who passed --repo that "no scope is configured" describes
    # a problem they do not have.
    if isinstance(scope, str) and scope.strip() == UNSCOPED_REPO_ID:
        console.print(
            f"[yellow]{UNSCOPED_REPO_ID} is reserved for quarantined memories of unknown "
            "origin, and is never searched.[/yellow]"
        )
        console.print("Pass [bold]--repo <name>[/bold] with your own project's scope.")
        raise typer.Exit(1)

    consequence = (
        "so this memory would be saved where nothing can retrieve it"
        if writing
        else "so there is nothing to search"
    )
    console.print(f"[yellow]No repository scope is configured, {consequence}.[/yellow]")
    console.print(
        "Run [bold]visp-memory init[/bold] in your project, or pass [bold]--repo <name>[/bold]."
    )
    raise typer.Exit(1)


def _parse_cli_datetime(value: Any) -> datetime:
    """Parse storage timestamps into aware UTC so age math matches utc_now()."""
    return parse_utc(value) or utc_now()


def _priority_label(priority: int) -> str:
    labels = {0: "LOW", 1: "NORMAL", 2: "HIGH", 3: "CRITICAL"}
    return labels.get(priority, str(priority))


def _intent_table(intents: List[dict], *, title: str = "Intents") -> Table:
    table = Table(title=title)
    table.add_column("ID", style="dim", width=10, overflow="ignore")
    table.add_column("Status", style="green", width=10)
    table.add_column("Priority", style="cyan", justify="center")
    table.add_column("Description")

    for intent in intents:
        priority = int(intent.get("priority", 1) or 0)
        table.add_row(
            str(intent["id"])[:10],
            intent.get("status", "active"),
            _priority_label(priority),
            intent["description"],
        )

    return table


def _memory_decay_preview(
    memory: Memory,
    *,
    repo: str = None,
    layer: str = None,
    category: str = None,
    limit: int = 25,
    halflife_days: int = None,
    min_importance: float = 0.1,
) -> list[dict]:
    """Calculate dry-run decay projections without mutating memories."""
    effective_halflife_days = max(1, halflife_days or memory.config.decay_halflife_days)
    min_importance = max(0.0, min(min_importance, 1.0))
    memories = memory._storage.list_memories(
        layer=layer,
        category=category,
        limit=10000,
        order_by="accessed_at ASC",
        repo_id=_repo_scope(memory, repo),
        status="active",
    )
    now = utc_now()
    rows = []

    for item in memories:
        current = float(item.get("importance", 0.5) or 0.0)
        accessed = _parse_cli_datetime(item.get("accessed_at") or item.get("created_at"))
        age_days = max(0.0, (now - accessed).total_seconds() / 86400)
        projected = projected_importance(
            importance=current,
            age_days=age_days,
            halflife_days=effective_halflife_days,
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

        rows.append(
            {
                "id": item["id"],
                "risk": risk,
                "current": current,
                "projected": projected,
                "drop": decay_amount,
                "age_days": age_days,
                "access_count": int(item.get("access_count") or 0),
                "layer": item.get("layer", "-"),
                "category": item.get("category", "-"),
                "content": " ".join(str(item.get("content", "")).split()),
            }
        )

    risk_rank = {"likely_to_decay": 0, "weakening": 1, "at_floor": 2, "stable": 3}
    rows.sort(key=lambda item: (risk_rank[item["risk"]], -item["drop"], item["projected"]))
    return rows[: max(1, min(limit, 100))]


def _format_age_days(days: float) -> str:
    if days >= 365:
        return f"{days / 365:.1f}y"
    return f"{days:.0f}d"


def _print_decay_preview(
    *,
    repo: str = None,
    layer: str = None,
    category: str = None,
    limit: int = 25,
    halflife_days: int = None,
    min_importance: float = 0.1,
):
    memory = get_memory()
    rows = _memory_decay_preview(
        memory,
        repo=repo,
        layer=layer,
        category=category,
        limit=limit,
        halflife_days=halflife_days,
        min_importance=min_importance,
    )
    effective_halflife_days = max(1, halflife_days or memory.config.decay_halflife_days)

    if not rows:
        console.print("[yellow]No active memories found[/yellow]")
        return

    console.print(
        Panel(
            (
                f"[bold]Memory decay preview[/bold]\n"
                f"Half-life: {effective_halflife_days}d | "
                f"Minimum importance: {min_importance:.2f} | "
                f"Decay enabled: {memory.config.decay_enabled}"
            )
        )
    )

    table = Table(title="Memory Health")
    table.add_column("ID", style="dim", width=10, overflow="ignore")
    table.add_column("Risk", style="yellow", width=17, no_wrap=True)
    table.add_column("Strength", style="cyan", width=20)
    table.add_column("Idle", justify="right", width=7)
    table.add_column("Content")

    for item in rows:
        content = f"[{item['layer']}] {item['content']}"
        if len(content) > 80:
            content = content[:77] + "..."
        table.add_row(
            str(item["id"])[:10],
            item["risk"],
            f"{item['current']:.2f} -> {item['projected']:.2f} (-{item['drop']:.2f})",
            _format_age_days(item["age_days"]),
            content,
        )

    console.print(table)
    console.print("\n[bold]Top candidates:[/bold]")
    for item in rows[:5]:
        content = item["content"]
        if len(content) > 120:
            content = content[:117] + "..."
        console.print(f"- [{item['risk']}] {str(item['id'])[:10]} {content}")


def _latest_memory(
    memory: Memory,
    *,
    repo: str = None,
    layer: str = None,
    category: str = None,
    status: str = "active",
) -> dict | None:
    memories = memory._storage.list_memories(
        layer=layer,
        category=category,
        limit=1,
        order_by="created_at DESC",
        repo_id=_repo_scope(memory, repo),
        status=status,
    )
    return memories[0] if memories else None


def _provider_error_message(provider: str, exc: Exception) -> str:
    """Return a generic, non-secret provider failure message."""
    text = str(exc)
    provider_label = (provider or "embedding").replace("-", " ").title()
    if "401" in text or "unauthorized" in text.lower():
        return f"{provider_label} rejected credentials."
    if "403" in text or "forbidden" in text.lower():
        return f"{provider_label} denied access."
    if "timeout" in text.lower():
        return f"{provider_label} connection timed out."
    return f"{provider_label} initialization failed."


def _embedding_provider_status(
    config: MemoryConfig, provider: str = None, *, verify: bool = True
) -> dict:
    """Build embedding provider diagnostics without leaking secrets."""
    configured_provider = (provider or config.embedding.provider or "sentence-transformers").lower()

    provider_config = config.embedding.model_copy()
    provider_config.provider = configured_provider

    diagnostics: dict[str, Any] = {
        "configured_provider": configured_provider,
        "effective_provider": configured_provider,
        "connected": False,
        "status": "pending",
        "status_message": "Not tested.",
        "error": None,
        "driver_model": None,
        "dimension": None,
        "credentials": {
            "api_key_configured": bool(
                provider_config.api_key
                or os.getenv("EMBEDDING_API_KEY")
                or os.getenv("OPENAI_API_KEY")
                or os.getenv("OPENROUTER_API_KEY")
            ),
            "api_base_set": bool(provider_config.api_base or os.getenv("EMBEDDING_API_BASE")),
            "api_host_set": bool(os.getenv("OLLAMA_HOST")),
        },
        "configured_model": provider_config.model,
    }

    try:
        from visp_memory.core.embeddings import get_embedding_provider

        provider_instance = get_embedding_provider(provider_config, verify=verify)
        effective_provider = getattr(provider_instance, "provider_name", configured_provider)
        diagnostics["effective_provider"] = effective_provider
        diagnostics["driver_model"] = getattr(provider_instance, "model", None)
        diagnostics["dimension"] = getattr(provider_instance, "dimension", None)
        if effective_provider in {"noop", "none"}:
            diagnostics["connected"] = False
            diagnostics["status"] = (
                "disabled" if configured_provider in {"noop", "none"} else "fallback"
            )
            diagnostics["status_message"] = (
                "Embeddings disabled."
                if diagnostics["status"] == "disabled"
                else "Fallback provider in use."
            )
        else:
            diagnostics["connected"] = True
            diagnostics["status"] = "connected"
            diagnostics["status_message"] = "Embedding provider connected."
    except Exception as exc:
        diagnostics["status"] = "failed"
        diagnostics["status_message"] = _provider_error_message(configured_provider, exc)
        diagnostics["error"] = exc.__class__.__name__

    return diagnostics


def _provider_report_payload() -> dict[str, Any]:
    """Collect provider diagnostics using local configuration only."""
    config = load_config()
    return {
        "version": __version__,
        "configured": {
            "project_name": config.project_name,
            "repo_id": config.repo_id,
            "project_type": config.project_type,
            "storage_mode": config.storage.mode,
            "storage_backend": config.storage.backend,
        },
        "embedding": _embedding_provider_status(config, provider=None),
        "storage_path": str(config.storage.data_dir),
    }


def _doctor_payload(verify_providers: bool = True) -> dict[str, Any]:
    """Collect diagnostics for doctor output."""
    config = load_config()
    return {
        "version": __version__,
        "configured": {
            "project_name": config.project_name,
            "repo_id": config.repo_id,
            "project_type": config.project_type,
        },
        "storage": _storage_doctor_status(config),
        "providers": {"embedding": _embedding_provider_status(config, verify=verify_providers)},
    }


def _storage_doctor_status(config: MemoryConfig) -> dict[str, Any]:
    """Collect lightweight storage diagnostics."""
    status = {
        "mode": config.storage.mode,
        "backend": config.storage.backend,
        "vector_db": config.storage.vector_db,
        "data_dir": str(config.storage.data_dir),
    }
    if config.storage.mode == "client":
        status["connected"] = None
        status["status"] = "remote_mode_not_checked"
        status["status_message"] = "Storage is client mode; diagnostics skip connectivity checks."
        return status

    try:
        status_path = Path(config.storage.data_dir)
        status_path.mkdir(parents=True, exist_ok=True)
        status["connected"] = True
        status["status"] = "accessible"
        status["status_message"] = "Data directory is writable."
    except Exception as exc:
        status["connected"] = False
        status["status"] = "unwritable_data_dir"
        status["status_message"] = str(exc)
    return status


def _print_provider_payload(payload: dict[str, Any], as_json: bool = False):
    if as_json:
        console.print_json(data=payload)
        return

    embedding = payload["embedding"]
    table = Table(title="Providers")
    table.add_column("Layer", style="cyan")
    table.add_column("Configured", style="green")
    table.add_column("Effective")
    table.add_column("Connected")
    table.add_column("Status")
    table.add_column("Message")

    table.add_row(
        "embedding",
        embedding["configured_provider"],
        embedding["effective_provider"],
        "yes" if embedding["connected"] else "no",
        embedding["status"],
        embedding["status_message"],
    )

    console.print(table)

    if embedding["status"] not in {"connected", "disabled", "fallback"}:
        maybe_error = embedding.get("error")
        if maybe_error:
            console.print(f"[yellow]Error[/yellow]: {maybe_error}")


def _print_doctor_payload(payload: dict[str, Any], as_json: bool = False):
    if as_json:
        # Ensure rich JSON output is stable and non-serializable fields are dropped.
        console.print_json(data=payload)
        return

    console.print("[bold]Visp Memory Diagnostics[/bold]")
    configured = payload["configured"]
    console.print(f"Project: {configured['project_name']} ({configured['project_type']})")
    if configured["repo_id"]:
        console.print(f"Repo: {configured['repo_id']}")

    storage = payload["storage"]
    console.print(
        f"Storage: mode={storage['mode']}, backend={storage['backend']}, path={storage['data_dir']}"
    )
    console.print(
        f"Storage status: {storage['status']} ({storage.get('status_message')})"
    )

    embedding = payload["providers"]["embedding"]
    console.print(
        f"Embedding: configured={embedding['configured_provider']}, "
        f"effective={embedding['effective_provider']}, connected={embedding['connected']}"
    )
    console.print(f"Embedding status: {embedding['status']} - {embedding['status_message']}")


# =============================================================================
# Init Command
# =============================================================================


@app.command()
def doctor(
    output_format: str = typer.Option(
        "text", "--format", "-f", help="Output format: text or json"
    ),
):
    """Run local diagnostics for storage and providers."""
    payload = _doctor_payload(verify_providers=True)
    as_json = output_format.lower() == "json"
    _print_doctor_payload(payload, as_json=as_json)


providers_app = typer.Typer(help="Provider diagnostics")
app.add_typer(providers_app, name="providers")


@providers_app.callback(invoke_without_command=True)
def providers(
    ctx: typer.Context,
    output_format: str = typer.Option(
        "text", "--format", "-f", help="Output format: text or json"
    ),
):
    """Show configured provider statuses."""
    if ctx.invoked_subcommand:
        return
    payload = _provider_report_payload()
    as_json = output_format.lower() == "json"
    _print_provider_payload(payload, as_json=as_json)


@providers_app.command("test")
def providers_test(
    provider: str = typer.Argument(..., help="Provider name to test"),
    output_format: str = typer.Option(
        "text", "--format", "-f", help="Output format: text or json"
    ),
):
    """Test a specific provider configuration."""
    config = load_config()
    payload = {"provider": provider.lower(), "result": _embedding_provider_status(config, provider)}
    as_json = output_format.lower() == "json"

    if as_json:
        console.print_json(data=payload)
    else:
        result = payload["result"]
        console.print(f"Provider test: {provider}")
        console.print(f"Configured: {result['configured_provider']}")
        console.print(f"Effective: {result['effective_provider']}")
        console.print(f"Connected: {'yes' if result['connected'] else 'no'}")
        console.print(f"Status: {result['status']}")
        console.print(f"Message: {result['status_message']}")
        if result["error"]:
            console.print(f"Error: {result['error']}")

    if not payload["result"]["connected"]:
        raise typer.Exit(1)



@app.command()
def init(
    project_type: str = typer.Option(
        "code", "--type", "-t", help="Project type: code, writing, research, general"
    ),
    name: str = typer.Option(None, "--name", "-n", help="Project name"),
    data_dir: str = typer.Option(".visp-memory", "--data", "-d", help="Data directory"),
    repo: str = typer.Option(None, "--repo", "-r", help="Default repository/project ID"),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing config file"),
    mine: bool = typer.Option(
        True,
        "--mine/--no-mine",
        help="Seed memory from this repository's git history and instruction files",
    ),
    mine_limit: int = typer.Option(
        400, "--mine-limit", help="Maximum commits to scan when seeding"
    ),
):
    """Initialize Visp Memory in the current directory.

    By default this also seeds the store from existing git history, so recall is useful
    immediately instead of after weeks of manual note-taking.
    """
    config_path = Path("visp-memory.yaml")

    if config_path.exists() and not force:
        console.print("[yellow]Config already exists. Use --force to overwrite.[/yellow]")
        raise typer.Exit(1)

    # Create config
    project_name = name or Path.cwd().name
    config = MemoryConfig(
        project_name=project_name,
        project_type=project_type,
        repo_id=repo or project_name,
    )
    config.storage.data_dir = Path(data_dir) / "data"

    # Create data directory
    config.storage.data_dir.mkdir(parents=True, exist_ok=True)

    # Save config using the save method (handles Path conversion)
    config.save(config_path)

    # Initialize memory to create tables
    memory = Memory(config=config)

    console.print(f"[green]Initialized Visp Memory in {config_path}[/green]")
    console.print(f"Data directory: {config.storage.data_dir}")

    ignored = ensure_gitignored(Path("."), config.storage.data_dir)
    if ignored:
        console.print(f"[dim]Added to .gitignore: {', '.join(ignored)}[/dim]")

    if not mine:
        return

    from visp_memory.capture.bootstrap import bootstrap_project

    with console.status("Reading project history..."):
        report = bootstrap_project(memory, Path.cwd(), limit=mine_limit)

    if report.errors and not report.total:
        # Seeding is a bonus, never a failure: init already succeeded above.
        console.print(f"[dim]Skipped history import: {report.errors[0]}[/dim]")
        return

    console.print()
    console.print(f"[green]{report.headline()}[/green]")
    for line in report.detail_lines():
        console.print(f"  [dim]•[/dim] {line}")
    if report.errors:
        console.print(f"  [dim]• {len(report.errors)} items could not be imported[/dim]")

    if report.total:
        console.print()
        console.print("[dim]Try:[/dim] visp-memory recall \"why\"")


# =============================================================================
# Record Commands
# =============================================================================


@app.command()
def record(
    event: str = typer.Argument(..., help="What happened"),
    category: str = typer.Option("note", "--category", "-c", help="Event category"),
    importance: float = typer.Option(0.5, "--importance", "-i", help="Importance (0.0-1.0)"),
    repo: str = typer.Option(None, "--repo", "-r", help="Repository context"),
):
    """Record an episodic memory (something that happened)."""
    memory = get_memory()
    repo = _require_repo_scope(memory, repo, writing=True)
    mem_id = memory.record(
        event,
        category=category,
        importance=importance,
        repo_id=repo,
        _write_channel=WriteChannel.CLI,
    )
    console.print(f"[green]Recorded:[/green] {event[:60]}...")
    console.print(f"[dim]ID: {mem_id}[/dim]")


@app.command()
def decision(
    what: str = typer.Argument(..., help="What was decided"),
    why: str = typer.Argument(..., help="Why this choice was made"),
    alternatives: List[str] = typer.Option(None, "--alt", "-a", help="Alternatives considered"),
    repo: str = typer.Option(None, "--repo", "-r", help="Repository context"),
):
    """Record an architecture/design decision."""
    memory = get_memory()
    repo = _require_repo_scope(memory, repo, writing=True)
    memory.decision(
        what,
        why,
        alternatives,
        repo_id=repo,
        _write_channel=WriteChannel.CLI,
    )
    console.print(f"[green]Decision recorded:[/green] {what}")
    console.print(f"[dim]Reasoning: {why}[/dim]")


@app.command()
def bug(
    description: str = typer.Argument(..., help="Bug description"),
    cause: str = typer.Option(None, "--cause", "-c", help="Root cause"),
    fix: str = typer.Option(None, "--fix", "-f", help="How it was fixed"),
    files: List[str] = typer.Option(None, "--file", help="Files involved"),
    repo: str = typer.Option(None, "--repo", "-r", help="Repository context"),
):
    """Record a bug discovery or fix."""
    memory = get_memory()
    repo = _require_repo_scope(memory, repo, writing=True)
    memory.episodic.bug(
        description,
        cause=cause,
        fix=fix,
        files=files,
        repo_id=_repo_scope(memory, repo),
        _write_channel=WriteChannel.CLI,
    )
    status = "fixed" if fix else "found"
    console.print(f"[green]Bug {status}:[/green] {description}")


# =============================================================================
# Learn Commands
# =============================================================================


@app.command()
def learn(
    knowledge: str = typer.Argument(..., help="The knowledge/fact/pattern"),
    category: BeliefType = typer.Option(
        BeliefType.FACT, "--category", "-c", help="Governed semantic belief type"
    ),
    importance: float = typer.Option(0.6, "--importance", "-i", help="Importance (0.0-1.0)"),
    repo: str = typer.Option(None, "--repo", "-r", help="Repository context"),
    authority_attestation_file: Optional[Path] = typer.Option(
        None,
        "--authority-attestation-file",
        exists=True,
        dir_okay=False,
        readable=True,
        help="Opaque signed prohibition envelope to send to the central verifier",
    ),
):
    """Establish semantic knowledge (something learned)."""
    memory = get_memory()
    repo = _require_repo_scope(memory, repo, writing=True)
    memory.learn(
        knowledge,
        category=category,
        importance=importance,
        repo_id=repo,
        authority_attestation=(
            authority_attestation_file.read_text(encoding="utf-8").strip()
            if authority_attestation_file is not None
            else None
        ),
        _write_channel=WriteChannel.CLI,
    )
    console.print(f"[green]Established:[/green] {knowledge[:60]}...")


def _claim_evidence(values: List[str]) -> List[dict[str, str]]:
    evidence = []
    for value in values:
        evidence_id, separator, content_hash = value.partition("=")
        if not separator or not evidence_id or not content_hash:
            raise typer.BadParameter(
                "each evidence value must be EVIDENCE_ID=CONTENT_HASH",
                param_hint="--evidence",
            )
        evidence.append({"id": evidence_id, "content_hash": content_hash})
    return evidence


@app.command("prohibition-sign")
def prohibition_sign(
    content: str = typer.Argument(..., help="Exact prohibition content to sign"),
    repo: str = typer.Option(..., "--repo", help="Exact repository ID"),
    evidence: List[str] = typer.Option(
        ..., "--evidence", help="Repeat EVIDENCE_ID=CONTENT_HASH for every evidence item"
    ),
    key_id: str = typer.Option(..., "--key-id", help="Public authority key identifier"),
    private_key_file: Path = typer.Option(
        ...,
        "--private-key-file",
        exists=True,
        dir_okay=False,
        readable=True,
        help="File containing exactly one base64 raw 32-byte Ed25519 private key",
    ),
    nonce: str = typer.Option(..., "--nonce", help="Unique authority nonce"),
    issued_at: str = typer.Option(..., "--issued-at", help="Signing timestamp"),
    environment: List[str] = typer.Option(
        None, "--environment", help="Repeat for every exact environment scope value"
    ),
    task_type: List[str] = typer.Option(
        None, "--task-type", help="Repeat for every exact task scope value"
    ),
    valid_from: Optional[str] = typer.Option(None, "--valid-from"),
    valid_to: Optional[str] = typer.Option(None, "--valid-to"),
    not_before: Optional[str] = typer.Option(None, "--not-before"),
    replaces_belief_id: Optional[str] = typer.Option(
        None, "--replaces-belief-id"
    ),
):
    """Create an offline Ed25519 prohibition envelope without opening storage."""
    claim = build_prohibition_claim(
        content=content,
        repo_id=repo,
        evidence=_claim_evidence(evidence),
        environment=environment,
        task_type=task_type,
        valid_from=valid_from,
        valid_to=valid_to,
        replaces_belief_id=replaces_belief_id,
    )
    envelope = sign_prohibition_attestation(
        claim,
        key_id=key_id,
        private_key=private_key_file.read_text(encoding="utf-8").strip(),
        nonce=nonce,
        issued_at=issued_at,
        not_before=not_before,
    )
    typer.echo(envelope)


@app.command()
def warn(
    area: str = typer.Argument(..., help="Area/file/module"),
    warning: str = typer.Argument(..., help="What to watch out for"),
    severity: float = typer.Option(0.7, "--severity", "-s", help="Severity (0.0-1.0)"),
    repo: str = typer.Option(None, "--repo", "-r", help="Repository context"),
):
    """Add a warning about a fragile area."""
    memory = get_memory()
    repo = _require_repo_scope(memory, repo, writing=True)
    memory.warn(
        area,
        warning,
        severity,
        repo_id=repo,
        _write_channel=WriteChannel.CLI,
    )
    console.print(f"[yellow]Warning added for {area}:[/yellow] {warning}")


@app.command()
def convention(
    rule: str = typer.Argument(..., help="The convention/rule"),
    rationale: str = typer.Option(None, "--rationale", "-r", help="Why this convention exists"),
    repo: str = typer.Option(None, "--repo", "-rp", help="Repository context"),
):
    """Establish a convention or best practice."""
    memory = get_memory()
    repo = _require_repo_scope(memory, repo, writing=True)
    memory.semantic.convention(
        rule,
        rationale,
        repo_id=_repo_scope(memory, repo),
        _write_channel=WriteChannel.CLI,
    )
    console.print(f"[green]Convention established:[/green] {rule}")


@app.command()
def issue(
    description: str = typer.Argument(..., help="Issue description"),
    workaround: str = typer.Option(None, "--workaround", "-w", help="How to work around it"),
    priority: float = typer.Option(0.5, "--priority", "-p", help="Priority (0.0-1.0)"),
    repo: str = typer.Option(None, "--repo", "-r", help="Repository context"),
):
    """Document a known issue."""
    memory = get_memory()
    repo = _require_repo_scope(memory, repo, writing=True)
    memory.semantic.known_issue(
        description,
        workaround,
        priority,
        repo_id=_repo_scope(memory, repo),
        _write_channel=WriteChannel.CLI,
    )
    console.print(f"[yellow]Known issue documented:[/yellow] {description}")


# =============================================================================
# Intent Commands
# =============================================================================

intent_app = typer.Typer(help="Intent lifecycle management")
app.add_typer(intent_app, name="intent")


@app.command()
def goal(
    description: str = typer.Argument(..., help="Goal description"),
    priority: int = typer.Option(
        1, "--priority", "-p", help="Priority (0=low, 1=normal, 2=high, 3=critical)"
    ),
    constraint: List[str] = typer.Option(None, "--constraint", "-c", help="Constraints"),
    repo: str = typer.Option(None, "--repo", "-r", help="Repository context"),
):
    """Set a goal/intent."""
    memory = get_memory()
    repo = _require_repo_scope(memory, repo, writing=True)
    memory.goal(description, priority=priority, constraints=constraint, repo_id=repo)
    console.print(f"[green]Goal set:[/green] {description}")


@app.command()
def focus(
    on: str = typer.Argument(..., help="What to focus on"),
    avoid: List[str] = typer.Option(None, "--avoid", "-a", help="What to avoid"),
    repo: str = typer.Option(None, "--repo", "-r", help="Repository context"),
):
    """Set current focus with things to avoid."""
    memory = get_memory()
    repo = _require_repo_scope(memory, repo, writing=True)
    memory.intent.set_focus(on, avoid, repo_id=_repo_scope(memory, repo))
    console.print(f"[green]Focus set:[/green] {on}")
    if avoid:
        console.print(f"[dim]Avoiding: {', '.join(avoid)}[/dim]")


@app.command()
def working(
    task: str = typer.Argument(..., help="What you're working on"),
    files: List[str] = typer.Option(None, "--file", "-f", help="Files being modified"),
    repo: str = typer.Option(None, "--repo", "-r", help="Repository context"),
):
    """Set current task."""
    memory = get_memory()
    repo = _require_repo_scope(memory, repo, writing=True)
    memory.working_on(task, files, repo_id=repo)
    console.print(f"[green]Working on:[/green] {task}")


@app.command()
def done():
    """Record a task completion outcome without changing intent status."""
    memory = get_memory()
    recorded = memory.done(actor_id="local-user", channel=WriteChannel.CLI)
    console.print(
        f"[green]Recorded {recorded} task outcome(s); intent status unchanged[/green]"
    )


@intent_app.command("list")
def intent_list(
    repo: str = typer.Option(None, "--repo", "-r", help="Filter by repository"),
    status: str = typer.Option(
        "active",
        "--status",
        help="Filter by status: active, completed, closed, or all",
    ),
):
    """List intents by status."""
    if status not in {"active", "completed", "closed", "all"}:
        raise typer.BadParameter("status must be active, completed, closed, or all")

    memory = get_memory()
    intents = memory._storage.get_active_intents(
        repo_id=_repo_scope(memory, repo),
        status=status,
    )

    if not intents:
        console.print(f"[yellow]No {status} intents[/yellow]")
        return

    console.print(_intent_table(intents, title=f"{status.title()} Intents"))


@intent_app.command("update")
def intent_update(
    intent_id: str = typer.Argument(..., help="Intent ID to update"),
    description: Optional[str] = typer.Option(
        None, "--description", "-d", help="Updated description"
    ),
    priority: Optional[int] = typer.Option(
        None,
        "--priority",
        "-p",
        help="Priority (0=low, 1=normal, 2=high, 3=critical)",
    ),
    status: Optional[str] = typer.Option(
        None,
        "--status",
        help=(
            "Deprecated compatibility input: record active, completed, or closed "
            "as outcome history; stored status is unchanged"
        ),
    ),
):
    """Update an intent by ID."""
    if status is not None and status not in {"active", "completed", "closed"}:
        raise typer.BadParameter("status must be active, completed, or closed")

    if description is None and priority is None and status is None:
        console.print("[yellow]No fields provided to update[/yellow]")
        raise typer.Exit(code=1)

    memory = get_memory()
    updated = memory.intent.update(
        intent_id,
        description=description,
        priority=priority,
        status=status,
        actor_id="local-user",
        channel=WriteChannel.CLI,
    )
    if not updated:
        console.print(f"[red]Intent not found:[/red] {intent_id}")
        raise typer.Exit(code=1)

    if status is not None:
        console.print(
            f"[green]Intent updated; {status} outcome recorded, status unchanged:[/green] "
            f"{intent_id}"
        )
    else:
        console.print(f"[green]Intent updated:[/green] {intent_id}")


@intent_app.command("complete")
def intent_complete(intent_id: str = typer.Argument(..., help="Intent ID to complete")):
    """Record a completion outcome without changing intent status."""
    memory = get_memory()
    completed = memory.intent.complete(
        intent_id,
        actor_id="local-user",
        channel=WriteChannel.CLI,
    )
    if not completed:
        console.print(f"[red]Intent not found:[/red] {intent_id}")
        raise typer.Exit(code=1)

    console.print(
        f"[green]Completion outcome recorded; intent status unchanged:[/green] {intent_id}"
    )


@intent_app.command("close")
def intent_close(intent_id: str = typer.Argument(..., help="Intent ID to close")):
    """Record a close outcome without changing intent status."""
    memory = get_memory()
    closed = memory.intent.close(
        intent_id,
        actor_id="local-user",
        channel=WriteChannel.CLI,
    )
    if not closed:
        console.print(f"[red]Intent not found:[/red] {intent_id}")
        raise typer.Exit(code=1)

    console.print(f"[green]Close outcome recorded; intent status unchanged:[/green] {intent_id}")


# =============================================================================
# Search Commands
# =============================================================================


@app.command()
def recall(
    query: str = typer.Argument(..., help="Search query"),
    limit: int = typer.Option(10, "--limit", "-n", help="Maximum results"),
    layer: str = typer.Option(None, "--layer", "-l", help="Filter by layer"),
    repo: str = typer.Option(None, "--repo", "-r", help="Filter by repository"),
    environment: str = typer.Option(
        None, "--environment", help="Runtime environment scope"
    ),
    task_type: str = typer.Option(None, "--task-type", help="Task type scope"),
    status: str = typer.Option("active", "--status", help="Filter by memory status"),
    log_utility: bool = typer.Option(False, "--log-utility", help="Log surfaced results"),
    task_id: str = typer.Option(
        None, "--task-id", help="Task ID to associate with surfaced results"
    ),
):
    """Search across all memories."""
    memory = get_memory()
    # Same refusal as the write side, and for the same reason: "repo_id is
    # required" names the field rather than the fix, and the person reading it
    # has no way to know that `init` is what they are missing.
    repo = _require_repo_scope(memory, repo)

    layers = [layer] if layer else None
    results = memory.recall(
        query,
        layers=layers,
        limit=limit,
        repo_id=repo,
        status=status,
        log_utility=log_utility,
        task_id=task_id,
        environment=environment,
        task_type=task_type,
    )

    if not results:
        console.print("[yellow]No results found[/yellow]")
        # Goals live in the intent layer and `recall` searches memories, so a
        # goal recorded seconds earlier comes back as a flat "No results found".
        # The layering is deliberate; the silent absence is not — a weak-model
        # evaluation recorded a goal, searched for it, saw nothing, and
        # concluded the write had failed.
        #
        # Point at the command that WOULD find it rather than merging the
        # layers, which would put unreviewed intents into recall output.
        matching = _intents_matching(memory, query, repo)
        if matching:
            noun = "intent" if matching == 1 else "intents"
            console.print(
                f"[dim]{matching} matching {noun} in the goal layer — "
                f"see [bold]visp-memory list-intents[/bold].[/dim]"
            )
        return

    from rich.box import ROUNDED

    table = Table(title=f"Search Results for '{query}'", box=ROUNDED)
    table.add_column("ID", style="dim", width=16)
    table.add_column("Layer", style="cyan", width=10)
    table.add_column("Category", style="green", width=12)
    table.add_column("Content")
    table.add_column("Score", justify="right", style="magenta")

    for r in results:
        score = f"{r.get('similarity', 0):.2f}" if r.get("similarity") else "-"
        content = r["content"].replace("\n", " ")
        if len(content) > 80:
            content = content[:77] + "..."

        table.add_row(r["id"], r["layer"], r.get("category", "-"), content, score)

    console.print(table)


@app.command()
def remember(
    repo: str = typer.Option(None, "--repo", "-r", help="Filter by repository"),
    layer: str = typer.Option(None, "--layer", "-l", help="Filter by layer"),
    category: str = typer.Option(None, "--category", "-c", help="Filter by category"),
    status: str = typer.Option("active", "--status", help="Filter by memory status"),
    full: bool = typer.Option(False, "--full", help="Show full content"),
):
    """Recall the latest memory in the current repository scope."""
    memory = get_memory()
    latest = _latest_memory(
        memory,
        repo=repo,
        layer=layer,
        category=category,
        status=status,
    )

    if latest is None:
        console.print("[yellow]No memories found[/yellow]")
        return

    content = latest["content"].replace("\n", " ")
    if not full and len(content) > 200:
        content = content[:197] + "..."

    table = Table(title="Latest Memory")
    table.add_column("Field", style="cyan")
    table.add_column("Value")
    table.add_row("ID", latest["id"])
    table.add_row("Layer", latest.get("layer", "-"))
    table.add_row("Category", latest.get("category", "-"))
    table.add_row("Repo", str(latest.get("repo_id") or "-"))
    table.add_row("Created", str(latest.get("created_at", "-")).replace("T", " "))
    table.add_row("Importance", f"{float(latest.get('importance', 0.5) or 0.0):.2f}")
    table.add_row("Content", content)
    console.print(table)


# =============================================================================
# Context Commands
# =============================================================================


@app.command()
def context(
    format: str = typer.Option("text", "--format", "-f", help="Output format: text or json"),
    no_history: bool = typer.Option(False, "--no-history", help="Exclude history"),
    no_knowledge: bool = typer.Option(False, "--no-knowledge", help="Exclude knowledge"),
    no_intent: bool = typer.Option(False, "--no-intent", help="Exclude intent"),
    environment: str = typer.Option(
        None, "--environment", help="Runtime environment scope"
    ),
    task_type: str = typer.Option(None, "--task-type", help="Task type scope"),
):
    """Get full context for LLM injection."""
    memory = get_memory()

    ctx = memory.context(
        include_history=not no_history,
        include_knowledge=not no_knowledge,
        include_intent=not no_intent,
        format=format,
        environment=environment,
        task_type=task_type,
    )

    if format == "json":
        console.print_json(json.dumps(ctx, default=str))
    else:
        console.print(Markdown(ctx))


@app.command()
def audit(
    tier: str = typer.Option(
        None, "--tier", help="Only show one provenance tier (authored/derived/assisted/external)"
    ),
    quarantined_only: bool = typer.Option(
        False, "--quarantined", help="Only show memories that can never be auto-injected"
    ),
    stale_only: bool = typer.Option(
        False, "--stale", help="Only show memories whose trust has decayed below the threshold"
    ),
    limit: int = typer.Option(40, "--limit", "-n", help="Maximum rows to display"),
    output_json: bool = typer.Option(False, "--json", help="Emit the audit as JSON"),
    repo: str = typer.Option(None, "--repo", "-r", help="Repository context"),
):
    """Show what is in memory, where it came from, and whether it can be injected.

    Memory that an assistant reads from is an attack surface: planting a small number of
    crafted records can capture a large share of retrievals. This command makes the store
    inspectable — provenance, trust, and injection eligibility for every memory.
    """
    from visp_memory.core.trust import DEFAULT_MIN_TRUST, Provenance, assess

    memory = get_memory()
    scope = _repo_scope(memory, repo)

    rows = []
    counts: dict[str, int] = {}
    quarantined = 0
    stale = 0
    redacted = 0

    for layer in ("episodic", "semantic", "intent"):
        for item in memory._storage.list_memories(layer=layer, repo_id=scope, limit=1000):
            assessment = assess(item)
            counts[assessment.tier.value] = counts.get(assessment.tier.value, 0) + 1
            if assessment.quarantined:
                quarantined += 1
            elif not assessment.injectable:
                stale += 1
            if any(str(f).startswith("secret_redacted") for f in (item.get("quality_flags") or [])):
                redacted += 1

            if tier and assessment.tier.value != tier.strip().lower():
                continue
            if quarantined_only and not assessment.quarantined:
                continue
            if stale_only and (assessment.quarantined or assessment.injectable):
                continue

            rows.append(
                {
                    "id": item.get("id"),
                    "layer": layer,
                    "category": item.get("category"),
                    "provenance": assessment.tier.value,
                    "trust": round(assessment.trust, 3),
                    "injectable": assessment.injectable,
                    "reason": assessment.reason,
                    "content": str(item.get("content", "")),
                }
            )

    rows.sort(key=lambda r: (r["injectable"], r["trust"]))

    if output_json:
        console.print_json(
            json.dumps(
                {
                    "total": sum(counts.values()),
                    "by_provenance": counts,
                    "quarantined": quarantined,
                    "pending_review": len(
                        memory._storage.list_memories(
                            repo_id=scope, status="quarantined", limit=1000
                        )
                    ),
                    "stale": stale,
                    "secret_redacted": redacted,
                    "min_trust": DEFAULT_MIN_TRUST,
                    "memories": rows[:limit],
                },
                default=str,
            )
        )
        return

    total = sum(counts.values())
    console.print(f"[bold]{total}[/bold] memories in scope")
    for name in (p.value for p in Provenance):
        if counts.get(name):
            console.print(f"  [dim]{name}:[/dim] {counts[name]}")
    console.print(
        f"  [yellow]quarantined:[/yellow] {quarantined} "
        f"[dim](never auto-injected — originated outside this repository)[/dim]"
    )
    # Lifecycle quarantine is a different axis from provenance quarantine, and
    # this command's whole purpose is that nothing in the store is invisible.
    # Proposals from `visp learn` sat at status='quarantined' while audit —
    # which iterates active rows — reported "quarantined: 0".
    pending_review = len(
        memory._storage.list_memories(repo_id=scope, status="quarantined", limit=1000)
    )
    if pending_review:
        console.print(
            f"  [yellow]pending review:[/yellow] {pending_review} "
            f"[dim]proposal(s) awaiting a human — see visp-memory review list[/dim]"
        )
    console.print(
        f"  [yellow]below trust threshold:[/yellow] {stale} "
        f"[dim](stale; still returned by explicit recall)[/dim]"
    )
    if redacted:
        console.print(
            f"  [yellow]credentials redacted:[/yellow] {redacted} "
            f"[dim](secrets removed at write time; content otherwise intact)[/dim]"
        )

    if not rows:
        console.print("\n[dim]No memories match the filter.[/dim]")
        return

    table = Table(title="Memory audit (least trusted first)")
    table.add_column("ID", style="dim", no_wrap=True)
    table.add_column("Provenance")
    table.add_column("Trust", justify="right")
    table.add_column("Inject")
    table.add_column("Content")

    for row in rows[:limit]:
        table.add_row(
            str(row["id"])[:8],
            row["provenance"],
            f"{row['trust']:.2f}",
            "[green]yes[/green]" if row["injectable"] else "[red]no[/red]",
            row["content"][:70],
        )
    console.print(table)

    if len(rows) > limit:
        console.print(f"[dim]… {len(rows) - limit} more (use --limit)[/dim]")


# =============================================================================
# The reviewed lifecycle — the half `contract propose` always promised
# =============================================================================
#
# `visp learn` and `visp-memory contract propose` both answer: "It becomes
# durable only after Memory's reviewed lifecycle accepts it." Until this group
# existed, no command implemented that lifecycle — every proposal sat at
# status='quarantined' forever, and even `audit` could not see it.
#
# This is NOT a laundering path. Rows in the reserved unscoped bucket carry
# Provenance.UNKNOWN and stay untouchable (tests/core/test_evidence_contract.py
# pins that trust control). Review operates only inside a real repository
# scope, on proposals that already belong to it.

review_app = typer.Typer(help="Review pending proposals from `visp learn` / `contract propose`.")
app.add_typer(review_app, name="review")


def _pending_proposal(memory, proposal_id: str, scope: str) -> dict:
    """Fetch a proposal the resolved scope is allowed to decide on, or refuse."""
    row = memory._storage.peek_memory(proposal_id)
    if row is None or row.get("status") != "quarantined":
        console.print(f"[yellow]No pending proposal with id {proposal_id}.[/yellow]")
        console.print("See what is waiting with [bold]visp-memory review list[/bold].")
        raise typer.Exit(1)
    if row.get("repo_id") != scope:
        # Never decide across scopes, and never touch the unscoped quarantine.
        console.print(
            f"[yellow]Proposal {proposal_id} does not belong to this repository scope.[/yellow]"
        )
        console.print("Review it from its own project, with its own scope.")
        raise typer.Exit(1)
    return row


@review_app.command("list")
def review_list(
    repo: str = typer.Option(None, "--repo", "-r", help="Repository context"),
):
    """List proposals awaiting a human decision in this repository."""
    memory = get_memory()
    scope = _require_repo_scope(memory, repo)
    pending = memory._storage.list_memories(repo_id=scope, status="quarantined", limit=200)
    if not pending:
        console.print("[dim]No proposals are waiting for review.[/dim]")
        return
    table = Table(title=f"Pending proposals ({scope})")
    table.add_column("ID", style="dim", no_wrap=True)
    table.add_column("Proposed", no_wrap=True)
    table.add_column("Content")
    for row in pending:
        table.add_row(
            str(row.get("id")),
            str(row.get("created_at", ""))[:19],
            str(row.get("content", ""))[:90],
        )
    console.print(table)
    console.print(
        "[dim]Accept with[/dim] visp-memory review accept <id> "
        "[dim]— reject with[/dim] visp-memory review reject <id>"
    )


@review_app.command("accept")
def review_accept(
    proposal_id: str = typer.Argument(..., help="Proposal ID from `review list`."),
    repo: str = typer.Option(None, "--repo", "-r", help="Repository context"),
):
    """Accept a proposal: it becomes durable and recall starts serving it."""
    memory = get_memory()
    scope = _require_repo_scope(memory, repo, writing=True)
    row = _pending_proposal(memory, proposal_id, scope)
    updated = memory._storage.update_memory(
        proposal_id,
        status="active",
        metadata={**(row.get("metadata") or {}), "review": {"decision": "accepted"}},
    )
    if not updated:
        console.print(f"[red]Could not update proposal {proposal_id}; nothing changed.[/red]")
        raise typer.Exit(1)
    console.print(f"[green]Accepted:[/green] {str(row.get('content', ''))[:70]}")
    console.print("[dim]Recall now serves it in this repository's scope.[/dim]")


@review_app.command("reject")
def review_reject(
    proposal_id: str = typer.Argument(..., help="Proposal ID from `review list`."),
    repo: str = typer.Option(None, "--repo", "-r", help="Repository context"),
):
    """Reject a proposal: it is kept for audit but never served."""
    memory = get_memory()
    scope = _require_repo_scope(memory, repo, writing=True)
    row = _pending_proposal(memory, proposal_id, scope)
    updated = memory._storage.update_memory(
        proposal_id,
        status="rejected",
        metadata={**(row.get("metadata") or {}), "review": {"decision": "rejected"}},
    )
    if not updated:
        console.print(f"[red]Could not update proposal {proposal_id}; nothing changed.[/red]")
        raise typer.Exit(1)
    console.print(f"[yellow]Rejected:[/yellow] {str(row.get('content', ''))[:70]}")


@app.command()
def preview(
    task: str = typer.Argument(..., help="The task an assistant would be working on"),
    file: List[str] = typer.Option(
        None, "--file", "-f", help="File(s) in scope (repeatable)"
    ),
    relaxed: bool = typer.Option(
        False, "--relaxed", help="Use the wider budget applied to explicit context requests"
    ),
    output_json: bool = typer.Option(False, "--json", help="Emit the decision as JSON"),
):
    """Show exactly what would be injected for a task, and why.

    Injection is unsolicited by nature, so it should be inspectable. This prints the
    selected memories, or the reason nothing was selected, along with how many candidates
    were considered and how they were filtered.
    """
    from visp_memory.core.injection import InjectionPolicy, format_injection, inject_for_task

    memory = get_memory()
    policy = InjectionPolicy.relaxed() if relaxed else InjectionPolicy()
    result = inject_for_task(memory, task=task, files=list(file) if file else None, policy=policy)

    if output_json:
        console.print_json(json.dumps(result.as_dict(), default=str))
        return

    if result.abstained:
        console.print(f"[yellow]Nothing would be injected[/yellow] — {result.reason}")
        console.print(f"[dim]{result.considered} candidates considered.[/dim]")
        return

    console.print(Markdown(format_injection(result)))
    console.print()
    console.print(f"[dim]{result.summary()}[/dim]")
    dropped = [
        (result.dropped_quarantined, "quarantined (originated outside this repository)"),
        (result.dropped_untrusted, "below the trust threshold (stale or unverified)"),
        (result.dropped_below_floor, "below the relevance floor"),
        (result.dropped_redundant, "redundant with a selected memory"),
        (result.dropped_over_budget, "over budget"),
    ]
    for count, why in dropped:
        if count:
            console.print(f"[dim]  • {count} {why}[/dim]")


@app.command()
def brief(
    task: str = typer.Argument(..., help="Concrete task to prepare"),
    repo: str = typer.Option(None, "--repo", "-r", help="Repository/project ID"),
    files: List[str] = typer.Option(None, "--file", "-f", help="Relevant file paths"),
    symbols: List[str] = typer.Option(None, "--symbol", "-s", help="Relevant symbols"),
    constraints: List[str] = typer.Option(
        None, "--constraint", "-c", help="Constraint that the task must preserve"
    ),
    intent_id: str = typer.Option(None, "--intent", help="Active intent ID override"),
    token_budget: int = typer.Option(
        2000, "--tokens", min=128, max=100000, help="Maximum injected context tokens"
    ),
    previous_fingerprint: str = typer.Option(
        None, "--previous-fingerprint", help="Prior brief fingerprint for a delta"
    ),
    min_confidence: float = typer.Option(
        0.0, "--min-confidence", min=0.0, max=1.0
    ),
    format: str = typer.Option("text", "--format", help="Output format: text or json"),
):
    """Prepare a cited, token-budgeted memory brief before work begins."""
    from visp_memory.core.task_brief import TaskMemoryBriefCompiler

    if format not in {"text", "json"}:
        console.print("[red]--format must be text or json[/red]")
        raise typer.Exit(2)
    memory = get_memory()
    result = TaskMemoryBriefCompiler(memory._storage).prepare(
        task,
        repo_id=_repo_scope(memory, repo),
        token_budget=token_budget,
        files=files or [],
        symbols=symbols or [],
        intent_id=intent_id,
        constraints=constraints or [],
        previous_fingerprint=previous_fingerprint,
        min_confidence=min_confidence,
    )
    if format == "json":
        console.print_json(json.dumps(result, default=str))
    elif result["unchanged"]:
        console.print(f"Task brief unchanged. Fingerprint: {result['fingerprint']}")
    else:
        console.print(Markdown(result["context"]))


@app.command()
def relevant(
    task: str = typer.Option(None, "--task", "-t", help="Task description"),
    files: List[str] = typer.Option(None, "--file", "-f", help="Files being worked on"),
):
    """Get memories relevant to a task or files."""
    memory = get_memory()

    if not task and not files:
        console.print("[yellow]Provide --task or --file[/yellow]")
        raise typer.Exit(1)

    results = memory.relevant_for(task=task, files=files)

    if results["knowledge"]:
        console.print(Panel("[bold]Relevant Knowledge[/bold]"))
        for k in results["knowledge"][:5]:
            console.print(f"  - {k['content'][:80]}...")

    if results["warnings"]:
        console.print(Panel("[bold yellow]Warnings[/bold yellow]"))
        for w in results["warnings"][:5]:
            console.print(f"  - {w['content']}")

    if results["history"]:
        console.print(Panel("[bold]Related History[/bold]"))
        for h in results["history"][:5]:
            console.print(f"  - [{h['category']}] {h['content'][:60]}...")


# =============================================================================
# Maintenance Commands
# =============================================================================


@app.command()
def stats():
    """Show memory statistics."""
    memory = get_memory()
    s = memory.stats()

    console.print(Panel("[bold]Memory Statistics[/bold]"))

    table = Table(show_header=False)
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right")

    table.add_row("Total Memories", str(s.get("total_memories", 0)))
    table.add_row("Active Intents", str(s.get("active_intents", 0)))
    table.add_row("Relationships", str(s.get("total_relationships", 0)))

    console.print(table)

    if s.get("memories_by_layer"):
        console.print("\n[bold]By Layer:[/bold]")
        for layer, count in s["memories_by_layer"].items():
            console.print(f"  {layer}: {count}")

    if s.get("memories_by_category"):
        console.print("\n[bold]By Category:[/bold]")
        for cat, count in list(s["memories_by_category"].items())[:10]:
            console.print(f"  {cat}: {count}")


@app.command("ingest-instructions")
def ingest_instructions_command(
    root: str = typer.Option(".", "--root", help="Project root to scan"),
    repo: str = typer.Option(None, "--repo", "-r", help="Repository scope"),
    importance: float = typer.Option(0.65, "--importance", min=0.0, max=1.0),
    dry_run: bool = typer.Option(False, "--dry-run", help="Report without storing"),
    format: str = typer.Option("text", "--format", "-f", help="Output format: text or json"),
):
    """Import CLAUDE.md / AGENTS.md / .cursor rules / copilot-instructions as seed memories."""
    from visp_memory.capture.instructions import ingest_instructions

    memory = get_memory()
    report = ingest_instructions(
        memory,
        root=root,
        repo_id=_repo_scope(memory, repo),
        importance=importance,
        dry_run=dry_run,
    )

    if format.lower() == "json":
        console.print_json(data=report.as_dict())
        return

    if not report.files:
        console.print("[yellow]No instruction files found (CLAUDE.md, AGENTS.md, ...).[/yellow]")
        return
    verb = "Would store" if dry_run else "Stored"
    console.print(
        f"[green]{verb} {report.stored} section(s) from {len(report.files)} file(s); "
        f"{report.skipped_unchanged} unchanged, {report.skipped_trivial} trivial skipped.[/green]"
    )
    for name in report.files:
        console.print(f"  - {name}")


@app.command("tokens")
def tokens(
    format: str = typer.Option("text", "--format", "-f", help="Output format: text or json"),
    repo: str = typer.Option(None, "--repo", "-r", help="Filter by repository"),
):
    """Show how many tokens the memory layer saves (consolidation + context compactness)."""
    memory = get_memory()
    report = memory.token_efficiency(repo_id=_repo_scope(memory, repo))

    if format.lower() == "json":
        console.print_json(data=report)
        return

    console.print(Panel("[bold]Token Efficiency[/bold]"))

    consolidation = report["consolidation"]
    context = report["context"]

    table = Table(show_header=True, header_style="bold")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right")
    table.add_row(
        f"Consolidation savings ({consolidation['consolidations']} compressions)",
        f"{consolidation['saved_tokens']} tokens ({consolidation['ratio'] * 100:.0f}%)",
    )
    table.add_row(
        "Context compactness vs full store",
        f"{context['compactness_ratio'] * 100:.0f}% "
        f"({context['context_tokens']} vs {context['full_store_tokens']} tokens)",
    )
    console.print(table)
    console.print(
        f"\n[green]Estimated tokens saved (auditable consolidation): "
        f"{report['total_saved_tokens']}[/green]"
    )
    console.print(
        "[dim]Consolidation savings are derived from compression lineage (token counts are "
        "estimates). Context compactness is descriptive only and is not counted as saved.[/dim]"
    )


@app.command("report")
def memory_report(
    format: str = typer.Option("text", "--format", "-f", help="Output format: text or json"),
    repo: str = typer.Option(None, "--repo", "-r", help="Filter by repository"),
    limit: int = typer.Option(10, "--limit", "-n", help="Maximum findings per section"),
):
    """Generate a deterministic memory intelligence report."""
    memory = get_memory()
    reporter = MemoryIntelligenceReporter(memory._storage)
    report = reporter.generate(repo_id=_repo_scope(memory, repo), limit=limit)

    if format.lower() == "json":
        console.print_json(data=report)
        return

    console.print(Markdown(MemoryIntelligenceReporter.format_text(report)))


@app.command()
def compress():
    """Compress old episodic memories into semantic knowledge."""
    memory = get_memory()
    created = memory.compress()
    console.print(f"[green]Created {len(created)} semantic memories from compression[/green]")


@app.command()
def decay():
    """Decay importance of old, unused memories."""
    memory = get_memory()
    affected = memory.decay()
    console.print(f"[green]Decayed {affected} memories[/green]")


@app.command("decay-preview")
def decay_preview(
    repo: str = typer.Option(None, "--repo", "-r", help="Filter by repository"),
    layer: str = typer.Option(None, "--layer", "-l", help="Filter by memory layer"),
    category: str = typer.Option(None, "--category", "-c", help="Filter by category"),
    limit: int = typer.Option(25, "--limit", "-n", min=1, max=100, help="Max results"),
    halflife_days: Optional[int] = typer.Option(
        None,
        "--halflife-days",
        help="Decay half-life override in days",
    ),
    min_importance: float = typer.Option(
        0.1,
        "--min-importance",
        min=0.0,
        max=1.0,
        help="Minimum projected importance floor",
    ),
):
    """Preview memory strength and decay risk without mutating memories."""
    _print_decay_preview(
        repo=repo,
        layer=layer,
        category=category,
        limit=limit,
        halflife_days=halflife_days,
        min_importance=min_importance,
    )


@app.command()
def dedup(
    layer: str = typer.Option("episodic", "--layer", "-l", help="Layer to check"),
    threshold: float = typer.Option(0.9, "--threshold", "-t", help="Similarity threshold"),
    merge: bool = typer.Option(
        False, "--merge", help="Merge duplicate groups into the first memory"
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip merge confirmation prompts"),
):
    """Find duplicate memories and optionally merge them."""

    memory = get_memory()
    duplicates = memory.deduplicate(layer=layer, threshold=threshold)

    if not duplicates:
        console.print("[green]No duplicates found.[/green]")
        return

    console.print(f"[yellow]Found {len(duplicates)} potential duplicates[/yellow]")

    # Simple listing for now
    for group in duplicates:
        console.print("--- Group ---")
        for mem in group:
            console.print(
                f"[{mem['id']}] {mem['content'][:50]}... ({mem.get('similarity', 0):.2f})"
            )

    if not merge:
        return

    merged_ids = []
    for group in duplicates:
        memory_ids = [mem["id"] for mem in group if mem.get("id")]
        if len(memory_ids) < 2:
            continue
        if not yes and not typer.confirm(
            f"Merge {len(memory_ids)} memories into {memory_ids[0]}?"
        ):
            continue
        merged_id = memory.deduplicator.merge_memories(memory_ids)
        if merged_id:
            merged_ids.append(merged_id)
            console.print(f"[green]Merged duplicate group into:[/green] {merged_id}")

    if merged_ids:
        console.print(f"[green]Merged {len(merged_ids)} duplicate group(s).[/green]")
    else:
        console.print("[yellow]No duplicate groups were merged.[/yellow]")


# =============================================================================
# Quality Commands
# =============================================================================

quality_app = typer.Typer(help="Memory quality management")
app.add_typer(quality_app, name="quality")
health_app = typer.Typer(help="Memory health and lifecycle previews")
app.add_typer(health_app, name="health")
feedback_app = typer.Typer(help="Recall utility feedback")
app.add_typer(feedback_app, name="feedback")


@health_app.command("decay-preview")
def health_decay_preview(
    repo: str = typer.Option(None, "--repo", "-r", help="Filter by repository"),
    layer: str = typer.Option(None, "--layer", "-l", help="Filter by memory layer"),
    category: str = typer.Option(None, "--category", "-c", help="Filter by category"),
    limit: int = typer.Option(25, "--limit", "-n", min=1, max=100, help="Max results"),
    halflife_days: Optional[int] = typer.Option(
        None,
        "--halflife-days",
        help="Decay half-life override in days",
    ),
    min_importance: float = typer.Option(
        0.1,
        "--min-importance",
        min=0.0,
        max=1.0,
        help="Minimum projected importance floor",
    ),
):
    """Preview memory strength and decay risk without mutating memories."""
    _print_decay_preview(
        repo=repo,
        layer=layer,
        category=category,
        limit=limit,
        halflife_days=halflife_days,
        min_importance=min_importance,
    )


@feedback_app.command("log")
def feedback_log(
    memory_id: str = typer.Option(..., "--memory-id", help="Memory ID to mark"),
    event: str = typer.Option(
        ...,
        "--event",
        help="surfaced, used, dismissed, task-linked, or outcome-linked",
    ),
    repo: str = typer.Option(None, "--repo", "-r", help="Repository context"),
    query: str = typer.Option(None, "--query", help="Query to hash for correlation"),
    task_id: str = typer.Option(None, "--task-id", help="Task ID to associate"),
    outcome: str = typer.Option(None, "--outcome", help="Outcome ID or label to associate"),
):
    """Log a recall utility feedback event."""
    memory = get_memory()
    try:
        event_id = memory.record_utility_feedback(
            memory_id=memory_id,
            event_type=event,
            repo_id=_repo_scope(memory, repo),
            query=query,
            task_id=task_id,
            outcome=outcome,
            metadata={"source": "cli"},
        )
    except (NotImplementedError, ValueError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    console.print(f"[green]Recorded feedback:[/green] {event} for {memory_id}")
    console.print(f"[dim]ID: {event_id}[/dim]")


@feedback_app.command("inspect")
def feedback_inspect(
    memory_id: str = typer.Option(None, "--memory-id", help="Filter by memory ID"),
    event: str = typer.Option(None, "--event", help="Filter by event type"),
    repo: str = typer.Option(None, "--repo", "-r", help="Filter by repository"),
    format: str = typer.Option("text", "--format", "-f", help="Output format: text or json"),
    limit: int = typer.Option(50, "--limit", "-n", min=0, max=500, help="Recent event limit"),
):
    """Inspect recall utility signals."""
    memory = get_memory()
    report = memory.inspect_utility_signals(
        memory_id=memory_id,
        repo_id=_repo_scope(memory, repo),
        event_type=event,
        limit=limit,
    )

    if format.lower() == "json":
        console.print_json(data=report)
        return

    summary = report["summary"]
    console.print(Panel("[bold]Recall Utility Signals[/bold]"))
    console.print(f"Events: {summary['total_events']}  Memories: {summary['memories']}")
    if summary["by_event_type"]:
        console.print("By event: " + ", ".join(
            f"{event_type}={count}"
            for event_type, count in sorted(summary["by_event_type"].items())
        ))

    table = Table()
    table.add_column("Memory ID", style="dim")
    table.add_column("Events", justify="right")
    table.add_column("Utility", justify="right")
    table.add_column("Rank Adj", justify="right")
    table.add_column("Counts")
    for signal in report["signals"]:
        counts = ", ".join(
            f"{event_type}={count}" for event_type, count in sorted(signal["counts"].items())
        )
        table.add_row(
            signal["memory_id"],
            str(signal["total_events"]),
            f"{signal['utility_score']:.2f}",
            f"{signal['utility_rank_adjustment']:.3f}",
            counts,
        )
    console.print(table)


@feedback_app.command("reset")
def feedback_reset(
    memory_id: str = typer.Option(None, "--memory-id", help="Filter by memory ID"),
    event: str = typer.Option(None, "--event", help="Filter by event type"),
    repo: str = typer.Option(None, "--repo", "-r", help="Filter by repository"),
    yes: bool = typer.Option(False, "--yes", help="Confirm reset"),
):
    """Reset recall utility signals matching filters."""
    if not yes:
        console.print("[yellow]Pass --yes to reset utility signals.[/yellow]")
        raise typer.Exit(1)

    memory = get_memory()
    deleted = memory.reset_utility_signals(
        memory_id=memory_id,
        repo_id=_repo_scope(memory, repo),
        event_type=event,
    )
    console.print(f"[green]Deleted {deleted} feedback events.[/green]")


@quality_app.command("conflicts")
def check_conflicts(
    content: str = typer.Argument(..., help="Statement to check for conflicts"),
    layer: str = typer.Option("semantic", "--layer", "-l", help="Layer to check against"),
):
    """Check if a statement conflicts with existing knowledge."""
    memory = get_memory()

    console.print(f"Checking for conflicts with: '{content}'")

    try:
        conflict = memory.check_conflict(content, layer=layer)

        if conflict:
            console.print(Panel("[bold red]Conflict Detected![/bold red]"))
            console.print(f"Reason: {conflict.get('reason')}")
            if conflict.get("conflicting_ids"):
                console.print(f"Conflicting IDs: {conflict.get('conflicting_ids')}")
        else:
            console.print("[green]No conflicts detected.[/green]")
    except Exception as e:
        console.print(f"[red]Error checking conflicts:[/red] {e}")
        raise typer.Exit(1)


@app.command()
def export(output: str = typer.Argument("memory-export.json", help="Output file path")):
    """Export this repository's memories to JSON.

    Scoped like every other read: memories recorded under another --repo live
    in the same physical store but are not part of this repository and are not
    exported. The old help text said "all memories", which silently promised
    more than the command has ever done.
    """
    memory = get_memory()
    memory.export(Path(output))
    console.print(f"[green]Exported to {output}[/green]")


@app.command(name="import")
def import_memories(input_file: str = typer.Argument(..., help="Input file path")):
    """Import memories from JSON export."""
    memory = get_memory()
    try:
        memory.import_memories(Path(input_file))
    except (FileNotFoundError, IsADirectoryError, json.JSONDecodeError, ValueError) as exc:
        console.print(f"[red]Import failed:[/red] {exc}")
        raise typer.Exit(code=1)
    console.print(f"[green]Imported from {input_file}[/green]")


# =============================================================================
# List Commands
# =============================================================================


@app.command("list")
def list_memories(
    layer: str = typer.Option(None, "--layer", "-l", help="Filter by layer"),
    category: str = typer.Option(None, "--category", "-c", help="Filter by category"),
    limit: int = typer.Option(20, "--limit", "-n", help="Max results"),
    full: bool = typer.Option(False, "--full", help="Show full content"),
    repo: str = typer.Option(None, "--repo", "-r", help="Filter by repository"),
    status: str = typer.Option("active", "--status", help="Filter by memory status"),
):
    """List recent memories."""
    from rich.box import ROUNDED

    memory = get_memory()

    # We need to access storage directly for list listing or expose it in Memory
    # Using private storage access for now as Memory doesn't have generic list
    memories = memory._storage.list_memories(
        layer=layer,
        category=category,
        limit=limit,
        order_by="created_at DESC",
        repo_id=_repo_scope(memory, repo),
        status=status,
    )

    if not memories:
        console.print("[yellow]No memories found[/yellow]")
        return

    table = Table(title="Recent Memories", box=ROUNDED)
    table.add_column("ID", style="dim", width=8, overflow="ignore")
    table.add_column("Time", style="dim", width=16)
    table.add_column("Layer", style="cyan", width=10)
    table.add_column("Category", style="green", width=12)
    table.add_column("Content")

    for m in memories:
        content = m["content"].replace("\n", " ")
        if not full and len(content) > 80:
            content = content[:77] + "..."

        table.add_row(
            m["id"][:8],
            m["created_at"][:16].replace("T", " "),
            m["layer"],
            m.get("category", "-"),
            content,
        )

    console.print(table)


@app.command()
def list_intents(
    repo: str = typer.Option(None, "--repo", "-r", help="Filter by repository"),
    status: str = typer.Option(
        "active",
        "--status",
        help="Filter by status: active, completed, closed, or all",
    ),
):
    """List intents/goals."""
    if status not in {"active", "completed", "closed", "all"}:
        raise typer.BadParameter("status must be active, completed, closed, or all")

    memory = get_memory()
    intents = memory._storage.get_active_intents(
        repo_id=_repo_scope(memory, repo),
        status=status,
    )

    if not intents:
        console.print(f"[yellow]No {status} intents[/yellow]")
        return

    console.print(_intent_table(intents, title=f"{status.title()} Intents"))


@app.command()
def list_warnings(repo: str = typer.Option(None, "--repo", "-r", help="Filter by repository")):
    """List all warnings."""
    memory = get_memory()
    warnings = memory.semantic.get_warnings(repo_id=_repo_scope(memory, repo))

    if not warnings:
        console.print("[green]No warnings[/green]")
        return

    console.print(Panel("[bold yellow]Warnings[/bold yellow]"))
    for w in warnings:
        console.print(f"  - {w['content']}")


@app.command()
def list_issues(repo: str = typer.Option(None, "--repo", "-r", help="Filter by repository")):
    """List known issues."""
    memory = get_memory()
    issues = memory.semantic.get_known_issues(repo_id=_repo_scope(memory, repo))

    if not issues:
        console.print("[green]No known issues[/green]")
        return

    console.print(Panel("[bold]Known Issues[/bold]"))
    for i in issues:
        console.print(f"  - {i['content']}")


# =============================================================================
# Recall Commands (Proactive)
# =============================================================================


@app.command()
def inject(
    files: List[str] = typer.Option(None, "--file", "-f", help="Files to get context for"),
    task: str = typer.Option(None, "--task", "-t", help="Task description"),
    format: str = typer.Option("markdown", "--format", help="Output format: markdown, plain, json"),
    max_length: int = typer.Option(2000, "--max-length", "-m", help="Max output length"),
    environment: str = typer.Option(
        None, "--environment", help="Runtime environment scope"
    ),
    task_type: str = typer.Option(None, "--task-type", help="Task type scope"),
):
    """
    Inject relevant memory context for files or task.

    This command surfaces warnings, bugs, decisions, and knowledge
    relevant to specific files or tasks without being asked.
    """
    from visp_memory.recall.proactive import ProactiveRecall

    memory = get_memory()
    recall = ProactiveRecall(memory, environment=environment, task_type=task_type)

    if files and task:
        # Comprehensive context for task with files
        context = recall.find_relevant_for_task(task_description=task, files=files)
        output = recall.format_injection(context, format=format, max_length=max_length)

    elif files:
        # File-specific context
        if len(files) == 1:
            context = recall.on_file_open(files[0])
        else:
            # Multiple files - aggregate
            context = {"warnings": [], "bugs": [], "decisions": [], "knowledge": []}
            for file in files:
                file_context = recall.on_file_open(file)
                for key in context:
                    context[key].extend(file_context.get(key, []))

        output = recall.format_injection(context, format=format, max_length=max_length)

    elif task:
        # Task-only context
        context = recall.find_relevant_for_task(task_description=task)
        output = recall.format_injection(context, format=format, max_length=max_length)

    else:
        console.print("[yellow]Provide --file or --task[/yellow]")
        raise typer.Exit(1)

    if output.strip():
        console.print(output)
    else:
        console.print("[dim]No relevant context found[/dim]")


@app.command("find-error")
def find_error(
    error: str = typer.Argument(..., help="Error message to search for"),
    error_type: str = typer.Option(None, "--type", "-t", help="Error type (e.g., TypeError)"),
    file: str = typer.Option(None, "--file", "-f", help="File where error occurred"),
    limit: int = typer.Option(5, "--limit", "-n", help="Max results"),
):
    """
    Find similar errors that occurred in the past.

    Searches for similar error messages along with their fixes
    and workarounds.
    """
    from visp_memory.recall.proactive import ProactiveRecall

    memory = get_memory()
    recall = ProactiveRecall(memory)

    similar = recall.on_error(
        error_message=error, error_type=error_type, file_path=file, limit=limit
    )

    if not similar:
        console.print("[yellow]No similar errors found[/yellow]")
        return

    console.print(Panel(f"[bold]Similar Past Errors ({len(similar)})[/bold]"))

    for i, err in enumerate(similar, 1):
        similarity = err.get("similarity", 0)
        content = err["content"]

        console.print(f"\n[cyan]{i}. Similarity: {similarity:.2f}[/cyan]")
        console.print(f"   {content[:200]}")

        # Show fix if available
        if "fix" in content.lower() or "solution" in content.lower():
            console.print("   [green]✓ Contains fix/solution[/green]")


# =============================================================================
# Capture Commands
# =============================================================================

# Create capture sub-app
capture_app = typer.Typer(help="Automatic memory capture from development activity")
app.add_typer(capture_app, name="capture")


@capture_app.command("git")
def capture_git(
    action: str = typer.Argument(..., help="Action: install, uninstall, sync, commit, merge"),
    commit_ref: str = typer.Option(
        "HEAD", "--ref", "-r", help="Commit reference for 'commit' action"
    ),
    since: str = typer.Option(None, "--since", "-s", help="Date for 'sync' (e.g., '1 week ago')"),
    until: str = typer.Option(None, "--until", "-u", help="End date for 'sync'"),
    limit: int = typer.Option(100, "--limit", "-n", help="Max commits for 'sync'"),
):
    """
    Git capture commands.

    Actions:
        install   - Install git hooks for automatic capture
        uninstall - Remove git hooks
        sync      - Sync from git history
        commit    - Manually capture a commit
        merge     - Manually capture a merge
    """
    try:
        from visp_memory.capture.git import GitCapture
    except ImportError:
        console.print("[red]Git capture not available.[/red]")
        console.print("Install with: [bold]pip install visp-memory[capture][/bold]")
        raise typer.Exit(1)

    memory = get_memory()

    try:
        git_capture = GitCapture(memory)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    if action == "install":
        console.print("Installing git hooks...")
        results = git_capture.install_hooks()

        for hook, success in results.items():
            if success:
                console.print(f"[green]✓[/green] Installed {hook}")
            else:
                console.print(f"[yellow]✗[/yellow] Failed to install {hook}")

        console.print("\n[green]Git hooks installed![/green]")
        console.print("Commits and merges will now be auto-captured.")

    elif action == "uninstall":
        console.print("Removing git hooks...")
        results = git_capture.uninstall_hooks()

        for hook, success in results.items():
            if success:
                console.print(f"[green]✓[/green] Removed {hook}")
            else:
                console.print(
                    f"[yellow]✗[/yellow] Could not remove {hook} (not installed by visp-memory)"
                )

        console.print("\n[green]Git hooks removed.[/green]")

    elif action == "sync":
        console.print(f"Syncing git history (limit: {limit})...")

        memory_ids = git_capture.sync_history(since=since, until=until, limit=limit)

        console.print(f"[green]Captured {len(memory_ids)} commits from history[/green]")

    elif action == "commit":
        console.print(f"Capturing commit {commit_ref}...")

        memory_id = git_capture.on_commit(commit_ref)

        if memory_id:
            console.print("[green]Captured commit[/green]")
            console.print(f"[dim]Memory ID: {memory_id}[/dim]")
        else:
            console.print("[yellow]Commit skipped[/yellow]")

    elif action == "merge":
        console.print("Capturing merge...")

        memory_id = git_capture.on_merge()

        if memory_id:
            console.print("[green]Captured merge[/green]")
            console.print(f"[dim]Memory ID: {memory_id}[/dim]")
        else:
            console.print("[yellow]Not a merge commit[/yellow]")

    else:
        console.print(f"[red]Unknown action: {action}[/red]")
        console.print("Valid actions: install, uninstall, sync, commit, merge")
        raise typer.Exit(1)


@capture_app.command("tests")
def capture_tests(report: str = typer.Argument("report.xml", help="Path to JUnit XML report")):
    """Capture test failures from JUnit XML report."""
    from visp_memory.capture.tests import TestCapture

    memory = get_memory()
    capture = TestCapture(memory)

    try:
        memory_ids = capture.on_pytest_session(report)
        if memory_ids:
            console.print(f"[green]Captured {len(memory_ids)} test failures[/green]")
        else:
            console.print("[green]No significant failures captured[/green]")
    except Exception as e:
        console.print(f"[red]Error parsing report:[/red] {e}")
        raise typer.Exit(1)


# =============================================================================
# Repository Commands (Phase 3.2)
# =============================================================================

repo_app = typer.Typer(help="Repository management")
app.add_typer(repo_app, name="repos")


@repo_app.command("register")
def register_repo(
    name: str = typer.Argument(..., help="Repository name"),
    url: str = typer.Option(None, "--url", "-u", help="Repository URL"),
    description: str = typer.Option(None, "--desc", "-d", help="Description"),
    team_id: str = typer.Option(None, "--team", "-t", help="Owning team ID"),
):
    """Register a new repository."""
    memory = get_memory()
    from visp_memory.core.repository import Repository

    repo = Repository(
        id=name,  # Use name as ID for simplicity in CLI
        name=name,
        url=url,
        description=description,
        team_id=team_id,
    )

    repo_id = memory.repos.register(repo)
    console.print(f"[green]Registered repository:[/green] {name}")
    console.print(f"[dim]ID: {repo_id}[/dim]")


@repo_app.command("list")
def list_repos(team: str = typer.Option(None, "--team", "-t", help="Filter by team ID")):
    """List registered repositories."""
    memory = get_memory()
    from rich.table import Table

    repos = memory.repos.list_all(team_id=team)

    if not repos:
        console.print("[yellow]No repositories found[/yellow]")
        return

    table = Table(title="Repositories")
    table.add_column("ID", style="cyan")
    table.add_column("Name", style="green")
    table.add_column("Team", style="magenta")
    table.add_column("Description")

    for r in repos:
        table.add_row(r.id, r.name, r.team_id or "-", r.description or "")

    console.print(table)


@repo_app.command("dependency")
def add_dependency(
    source: str = typer.Argument(..., help="Source repository ID"),
    target: str = typer.Argument(..., help="Target repository ID"),
    type: str = typer.Option("depends_on", "--type", "-t", help="Dependency type"),
):
    """Add a dependency between repositories."""
    memory = get_memory()
    from visp_memory.core.repository import DependencyType, RepositoryDependency

    try:
        dep = RepositoryDependency(
            source_repo_id=source,
            target_repo_id=target,
            dependency_type=DependencyType(type),
        )
        memory.repos.add_dependency(dep)
        console.print(f"[green]Added dependency:[/green] {source} -> {target} ({type})")
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@repo_app.command("context")
def repo_context(
    repo: str = typer.Argument(..., help="Repository ID"),
    format: str = typer.Option("text", "--format", "-f", help="Output format (text/json)"),
):
    """Get cross-repository context (warnings from dependencies)."""
    memory = get_memory()
    from visp_memory.core.cross_repo import CrossRepoContext

    ctx_manager = CrossRepoContext(memory.repos.storage, memory.repos)
    context = ctx_manager.get_context_for_repo(repo)

    if "error" in context:
        console.print(f"[red]{context['error']}[/red]")
        return

    if format == "json":
        console.print_json(json.dumps(context, default=str))
        return

    console.print(Panel(f"[bold]Cross-Repo Context for {repo}[/bold]"))

    if context["warnings"]:
        console.print("\n[yellow]Warnings from dependencies:[/yellow]")
        for w in context["warnings"]:
            console.print(f"  - [{w.get('repo_id', '?')}] {w['content']}")

    if context["breaking_changes"]:
        console.print("\n[red]Breaking Changes:[/red]")
        for b in context["breaking_changes"]:
            console.print(f"  - [{b.get('repo_id', '?')}] {b['content']}")

    if not context["warnings"] and not context["breaking_changes"]:
        console.print("[green]No warnings or breaking changes found in dependencies.[/green]")


# =============================================================================
# Team Commands (Phase 3.3)
# =============================================================================

team_app = typer.Typer(help="Team and user management")
app.add_typer(team_app, name="teams")

admin_app = typer.Typer(help="Dashboard administrator management")
app.add_typer(admin_app, name="admin")


@admin_app.command("create")
def create_dashboard_admin(
    username: str = typer.Option("admin", "--username", help="Administrator username"),
    password: Optional[str] = typer.Option(
        None,
        "--password",
        envvar="VISP_MEMORY_ADMIN_PASSWORD",
        help="Password, preferably supplied through VISP_MEMORY_ADMIN_PASSWORD",
        hide_input=True,
    ),
):
    """Create the first dashboard administrator account."""
    from visp_memory.server.auth_store import AuthStore

    if not password:
        password = typer.prompt("Password", hide_input=True, confirmation_prompt=True)
    config = load_config()
    auth_store = AuthStore(config.storage.data_dir / "auth.db")
    try:
        account = auth_store.create_account(
            username=username,
            password=password,
            role="admin",
        )
    except ValueError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(code=1) from error
    console.print(f"[green]Created dashboard administrator:[/green] {account['username']}")


@team_app.command("create")
def create_team(
    name: str = typer.Argument(..., help="Team name"),
    description: str = typer.Option(None, "--desc", "-d", help="Description"),
):
    """Create a new team."""
    memory = get_memory()
    from visp_memory.core.team import Team

    team = Team(id=name.lower().replace(" ", "-"), name=name, description=description)

    team_id = memory.teams.create_team(team)
    console.print(f"[green]Created team:[/green] {name}")
    console.print(f"[dim]ID: {team_id}[/dim]")


@team_app.command("user")
def create_user(
    username: str = typer.Argument(..., help="Username"),
    email: str = typer.Option(None, "--email", "-e", help="Email address"),
    name: str = typer.Option(None, "--name", "-n", help="Display name"),
):
    """Create or update a user."""
    memory = get_memory()
    from visp_memory.core.team import User

    user = User(id=username, username=username, email=email, display_name=name or username)

    memory.teams.create_user(user)
    console.print(f"[green]User saved:[/green] {username}")


@team_app.command("add-member")
def add_team_member(
    team: str = typer.Argument(..., help="Team ID"),
    user: str = typer.Argument(..., help="User ID/Username"),
):
    """Add a user to a team."""
    memory = get_memory()

    if memory.teams.add_member(team, user):
        console.print(f"[green]Added {user} to team {team}[/green]")
    else:
        console.print("[red]Failed to add member (check IDs)[/red]")
        raise typer.Exit(1)


@team_app.command("list")
def list_user_teams(
    user: str = typer.Argument("current", help="User ID (defaults to current if authenticated)"),
):
    """List teams for a user."""
    memory = get_memory()

    if user == "current":
        # Check if we have a config user (only in authenticated contexts)
        # For local, this might be ambiguous. Let's warn.
        console.print("[yellow]Please provide specific user ID for local mode[/yellow]")
        raise typer.Exit(1)

    teams = memory.teams.get_user_teams(user)

    if not teams:
        console.print(f"[yellow]No teams found for user {user}[/yellow]")
        return

    console.print(f"[bold]Teams for {user}:[/bold]")
    for t in teams:
        console.print(f"  - {t.name} ({t.id})")


# =============================================================================
# Conversation Capture
# =============================================================================


@capture_app.command("conversation")
def capture_conversation(
    file: str = typer.Argument(..., help="Path to conversation log file (text, markdown, json)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Analyze without saving"),
):
    """
    Capture memories from a conversation log.

    Uses configured LLM to extract decisions, learnings, bugs, and tasks.
    """
    try:
        from visp_memory.capture.conversation import ConversationCapture
    except ImportError:
        console.print("[red]Import Error: Could not load ConversationCapture[/red]")
        raise typer.Exit(1)

    memory = get_memory()

    try:
        capturer = ConversationCapture(memory)
        console.print(f"Analyzing {file}...")

        result = capturer.parse_file(file, dry_run=dry_run)

        console.print(
            Panel(f"[bold]Extraction Results ({'Dry Run' if dry_run else 'Saved'})[/bold]")
        )
        console.print(f"Decisions: {result['decisions']}")
        console.print(f"Learnings: {result['learnings']}")
        console.print(f"Bugs:      {result['bugs']}")
        console.print(f"Tasks:     {result['tasks']}")

        if dry_run and result["raw"]:
            console.print("\n[dim]Raw Extraction:[/dim]")
            console.print_json(data=result["raw"])

    except ValueError as e:
        console.print(f"[red]Configuration Error:[/red] {e}")
        console.print("Ensure VISP_MEMORY_CAPTURE_LLM_PROVIDER is set.")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


# =============================================================================
# Hooks Commands (LLM Tool Integration)
# =============================================================================

# Create hooks sub-app
hooks_app = typer.Typer(help="Integration with LLM tools (Claude Code, Codex, Cursor, Aider)")
app.add_typer(hooks_app, name="hooks")

# Runtime entry points invoked BY Claude Code (configured in .claude/settings.json
# by `visp-memory hooks install claude-code`). They read the hook payload from
# stdin, print hook JSON to stdout, and always exit 0: a memory failure must
# never break the user's coding session.
hook_runtime_app = typer.Typer(help="Hook runtime endpoints called by Claude Code (stdin JSON)")
app.add_typer(hook_runtime_app, name="hook")


def _run_hook_handler(handler_name: str) -> None:
    import sys

    try:
        from visp_memory.hooks import claude_code_auto

        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        handler = getattr(claude_code_auto, handler_name)
        output = handler(payload)
        if output:
            sys.stdout.write(json.dumps(output))
    except Exception:  # fail-open by contract
        pass


@hook_runtime_app.command("session-start")
def hook_session_start():
    """Inject project memory context at session start (called by Claude Code)."""
    _run_hook_handler("handle_session_start")


@hook_runtime_app.command("pre-tool-use")
def hook_pre_tool_use():
    """Inject file-relevant memory before Read/Edit/Write (called by Claude Code)."""
    _run_hook_handler("handle_pre_tool_use")


def _get_hook_adapter(
    tool: str,
    memory: Memory,
    server_url: str = "http://127.0.0.1:8000",
    repo_id: str = None,
    config_path: Path = None,
    dry_run: bool = False,
):
    from visp_memory.hooks import get_adapter

    if tool.lower() == "codex":
        return get_adapter(
            tool,
            memory=memory,
            server_url=server_url,
            repo_id=repo_id,
            config_path=config_path,
            dry_run=dry_run,
        )

    return get_adapter(tool, memory=memory)


@hooks_app.command("install")
def hooks_install(
    tool: str = typer.Argument(..., help="Tool name: claude-code, codex, cursor, aider, generic"),
    server_url: str = typer.Option(
        "http://127.0.0.1:8000",
        "--server-url",
        help="Memory server URL for Codex client-mode MCP config",
    ),
    repo_id: str = typer.Option(None, "--repo-id", help="Repository ID for Codex memory scope"),
    config_path: Path = typer.Option(
        None, "--config-path", help="Codex config path; defaults to ~/.codex/config.toml"
    ),
    auto_inject: bool = typer.Option(
        True,
        "--auto-inject/--no-auto-inject",
        help=(
            "claude-code only: also install real SessionStart/PreToolUse hooks in "
            ".claude/settings.json so memory is injected automatically"
        ),
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview installation without writing"),
):
    """
    Install hooks for an LLM tool.

    Installs context injection for the specified tool.
    """
    memory = get_memory()

    try:
        adapter = _get_hook_adapter(tool, memory, server_url, repo_id, config_path, dry_run)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    console.print(f"{'Previewing' if dry_run else 'Installing'} {tool} integration...")

    try:
        results = adapter.install()
    except (OSError, UnicodeError) as e:
        console.print(f"[red]Error:[/red] failed to install {tool} integration: {e}")
        raise typer.Exit(1)

    for component, success in results.items():
        if success:
            console.print(f"[green]✓[/green] {component}")
        else:
            console.print(f"[yellow]✗[/yellow] {component}")

    if tool.lower() == "claude-code" and auto_inject and not dry_run:
        from visp_memory.hooks.claude_code_auto import install_auto_inject_hooks

        try:
            settings_path = install_auto_inject_hooks(Path.cwd())
            console.print(f"[green]✓[/green] auto-inject hooks ({settings_path})")
            console.print(
                "[dim]SessionStart injects project memory; PreToolUse injects "
                "file-relevant warnings before Read/Edit/Write. Requires `visp-memory` "
                "on PATH for Claude Code to invoke.[/dim]"
            )
        except ValueError as e:
            console.print(f"[yellow]✗ auto-inject hooks skipped:[/yellow] {e}")

    console.print(f"\n[green]{tool} integration {'validated' if dry_run else 'installed'}![/green]")
    console.print(f"Context file: {adapter.get_context_file_path()}")
    if tool.lower() == "codex":
        console.print(f"Codex config: {adapter.config_path}")
        if dry_run:
            console.print("\n[dim]Managed MCP config block:[/dim]")
            console.print(adapter.config_block(), markup=False)
        else:
            console.print("[yellow]Restart Codex after changing MCP configuration.[/yellow]")
    console.print(f"\nUpdate context with: [bold]visp-memory hooks update {tool}[/bold]")


@hooks_app.command("uninstall")
def hooks_uninstall(
    tool: str = typer.Argument(..., help="Tool name: claude-code, codex, cursor, aider, generic"),
    config_path: Path = typer.Option(
        None, "--config-path", help="Codex config path; defaults to ~/.codex/config.toml"
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview removal without writing"),
):
    """Remove hooks for an LLM tool."""
    memory = get_memory()

    try:
        adapter = _get_hook_adapter(tool, memory, config_path=config_path, dry_run=dry_run)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    console.print(f"{'Previewing removal of' if dry_run else 'Uninstalling'} {tool} integration...")

    try:
        results = adapter.uninstall()
    except (OSError, UnicodeError) as e:
        console.print(f"[red]Error:[/red] failed to uninstall {tool} integration: {e}")
        raise typer.Exit(1)

    for component, success in results.items():
        if success:
            console.print(f"[green]✓[/green] {component} removed")
        else:
            console.print(f"[yellow]✗[/yellow] {component} not found")

    if tool.lower() == "claude-code" and not dry_run:
        from visp_memory.hooks.claude_code_auto import uninstall_auto_inject_hooks

        if uninstall_auto_inject_hooks(Path.cwd()):
            console.print("[green]✓[/green] auto-inject hooks removed")

    console.print(f"\n[green]{tool} integration removed.[/green]")


@hooks_app.command("update")
def hooks_update(
    tool: str = typer.Argument(..., help="Tool name: claude-code, codex, cursor, aider, generic"),
    files: List[str] = typer.Option(None, "--file", "-f", help="Files being worked on"),
    task: str = typer.Option(None, "--task", "-t", help="Task description"),
):
    """
    Update context for an LLM tool.

    Refreshes the tool's context file with current memory.
    """
    from visp_memory.hooks import get_adapter

    memory = get_memory()

    try:
        adapter = get_adapter(tool, memory=memory)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    if not adapter.is_installed():
        console.print(f"[yellow]{tool} integration not installed.[/yellow]")
        console.print(f"Install with: [bold]visp-memory hooks install {tool}[/bold]")
        raise typer.Exit(1)

    console.print(f"Updating {tool} context...")

    try:
        success = adapter.update_context(files=files, task=task)
    except (OSError, UnicodeError) as e:
        console.print(f"[red]Error:[/red] failed to update {tool} context: {e}")
        raise typer.Exit(1)

    if success:
        console.print("[green]✓ Context updated[/green]")
        console.print(f"File: {adapter.get_context_file_path()}")
    else:
        console.print("[red]✗ Failed to update context[/red]")
        raise typer.Exit(1)


@hooks_app.command("list")
def hooks_list():
    """List available LLM tool integrations."""
    console.print(Panel("[bold]Available LLM Tool Integrations[/bold]"))

    tools = [
        ("claude-code", "Claude Code / Claude Desktop", "CLAUDE.md"),
        ("codex", "Codex MCP + project instructions", "AGENTS.md + ~/.codex/config.toml"),
        ("cursor", "Cursor IDE", ".cursorrules"),
        ("aider", "Aider", ".aider"),
        ("generic", "Generic (any context file)", "Custom file"),
    ]

    table = Table(show_header=True)
    table.add_column("Tool", style="cyan")
    table.add_column("Description")
    table.add_column("Context File", style="dim")

    for tool, desc, file in tools:
        table.add_row(tool, desc, file)

    console.print(table)

    console.print("\n[dim]Install with:[/dim] [bold]visp-memory hooks install <tool>[/bold]")


# =============================================================================
# MCP Server Command
# =============================================================================


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host", "-h", help="Host to bind"),
    port: int = typer.Option(8000, "--port", "-p", help="Port to bind"),
    reload: bool = typer.Option(False, "--reload", help="Enable auto-reload"),
):
    """Run the Central Memory Server (FastAPI REST API).

    This serves the HTTP API and dashboard. To run the MCP stdio server for an
    assistant, use the separate ``visp-memory-mcp`` console script instead.
    """
    _ensure_serveable_auth_config(host)

    console.print(f"[green]Starting Central Memory Server at http://{host}:{port}[/green]")
    try:
        import uvicorn

        uvicorn.run("visp_memory.server.app:app", host=host, port=port, reload=reload)
    except ImportError:
        console.print("[red]uvicorn not installed.[/red]")
        console.print("Install with: [bold]pip install visp-memory[api][/bold]")
        raise typer.Exit(1)


_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _ensure_serveable_auth_config(host: str) -> None:
    """Keep ``serve`` usable out of the box without exposing an open public server.

    With auth enabled (the default) and no JWT secret, API keys, or anonymous
    access configured, no request could authenticate. For a loopback bind we
    start in open local mode (anonymous) with a warning so the documented quick
    start and standalone ``start-server.sh`` work without setup; for a
    non-loopback bind we refuse, since that would expose an unauthenticated
    server on a reachable interface.
    """
    config = load_config()
    server = config.server
    has_credentials = (
        bool(server.jwt_secret)
        or bool(server.api_keys)
        or bool(getattr(config.storage, "api_key", None))
        or server.allow_anonymous
    )
    if not (server.auth_enabled and not has_credentials):
        return

    if host in _LOOPBACK_HOSTS:
        console.print(
            "[yellow]Auth is enabled but no credentials are configured; starting in open "
            "local mode (anonymous access) on the loopback interface.[/yellow]\n"
            "Set [bold]VISP_MEMORY_JWT_SECRET[/bold] or [bold]VISP_MEMORY_SERVER_API_KEYS[/bold] "
            "for authenticated use, or [bold]VISP_MEMORY_SERVER_AUTH_ENABLED=false[/bold] to "
            "silence this."
        )
        # The FastAPI app reads config via load_config(); enabling anonymous here
        # (and for the reload subprocess, which inherits the environment) makes the
        # local server usable without committing credentials.
        os.environ["VISP_MEMORY_SERVER_ALLOW_ANONYMOUS"] = "true"
        return

    console.print(
        f"[red]Refusing to start: authentication is enabled but no credentials are configured, "
        f"and the server would bind to a non-loopback interface ({host}), exposing an "
        f"unauthenticated server.[/red]\n"
        "Configure one of the following first:\n"
        "  - [bold]VISP_MEMORY_JWT_SECRET[/bold] for JWT auth\n"
        "  - [bold]VISP_MEMORY_SERVER_API_KEYS[/bold] for API-key auth\n"
        "  - [bold]VISP_MEMORY_SERVER_ALLOW_ANONYMOUS=true[/bold] to allow anonymous\n"
        "  - [bold]VISP_MEMORY_SERVER_AUTH_ENABLED=false[/bold] to disable auth\n"
        "Or bind to 127.0.0.1 for local-only use."
    )
    raise typer.Exit(1)


# =============================================================================
# Dashboard Commands
# =============================================================================


@app.command()
def status():
    """Show system status dashboard."""
    from rich.align import Align
    from rich.box import ROUNDED
    from rich.text import Text

    memory = get_memory()
    stats = memory.stats()
    repo_id = memory.config.repo_id

    # 1. System Info
    info_table = Table(box=None, show_header=False, padding=(0, 2))
    info_table.add_row("Active Config", memory.config.storage.data_dir.name)
    info_table.add_row("Total Memories", str(stats.get("total_memories", 0)))
    info_table.add_row("Active Intents", str(stats.get("active_intents", 0)))
    info_table.add_row("Relationships", str(stats.get("total_relationships", 0)))

    # 2. Key Stats (Memories by Layer)
    layer_table = Table(title="Memories by Layer", box=ROUNDED, show_header=True)
    layer_table.add_column("Layer", style="cyan")
    layer_table.add_column("Count", justify="right")

    for layer, count in stats.get("memories_by_layer", {}).items():
        layer_table.add_row(layer.capitalize(), str(count))

    # 3. Recent Activity (Last 5 Episodic)
    recent = memory.episodic.recent(limit=5, repo_id=repo_id)
    activity_table = Table(title="Recent Activity", box=ROUNDED, show_header=True, expand=True)
    activity_table.add_column("Time", style="dim", width=12)
    activity_table.add_column("Category", style="green", width=10)
    activity_table.add_column("Event")

    for m in recent:
        # Simple time format (just HH:MM or date if old)
        # For now just truncated string
        time_str = m["created_at"][11:16]
        activity_table.add_row(time_str, m["category"], m["content"][:60])

    # 4. Current Context (Intents/Warnings)
    intent_summary = memory.intent.summarize(repo_id=repo_id)

    focus_text = "[italic dim]No current focus[/]"
    if intent_summary.get("focus"):
        focus_text = f"[bold cyan]{intent_summary['focus']['description']}[/bold cyan]"

    task_text = "[italic dim]No active task[/]"
    if intent_summary.get("current_task"):
        task_text = f"[bold yellow]{intent_summary['current_task']['description']}[/bold yellow]"

    context_panel = Panel(
        Group(
            Text("Current Focus:", style="dim"),
            Text.from_markup(focus_text),
            Text(""),
            Text("Working On:", style="dim"),
            Text.from_markup(task_text),
        ),
        title="Active Context",
        box=ROUNDED,
    )

    # Layout Construction
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3), Layout(name="main", ratio=1), Layout(name="footer", size=3)
    )

    layout["header"].update(
        Panel(
            Align.center(
                f"[bold blue]Visp Memory System[/bold blue] - {memory.config.storage.data_dir}"
            ),
            box=ROUNDED,
            style="white on black",
        )
    )

    layout["main"].split_row(Layout(name="left", ratio=1), Layout(name="right", ratio=2))

    layout["left"].split_column(Layout(name="context", ratio=1), Layout(name="stats", ratio=1))

    layout["left"]["context"].update(context_panel)
    layout["left"]["stats"].update(layer_table)

    layout["right"].update(activity_table)

    layout["footer"].update(
        Align.center(
            "[dim]Run 'visp-memory help' for commands | 'visp-memory recall' to search[/dim]"
        )
    )

    console.print(layout)


# =============================================================================
# The machine contract (P10-US-07, contract version 1.0)
# =============================================================================
#
# `visp-memory contract …` is the versioned, machine-readable surface the Visp
# coordinator (visp-hyper-agent >= 0.7.0) speaks. It is deliberately tiny:
# recall (read) and propose (a quarantined proposal through the reviewed
# lifecycle — never a direct durable write). The envelope is versioned so a
# consumer can detect an incompatible Memory instead of misreading it.

MEMORY_CONTRACT_VERSION = "1.0"

contract_app = typer.Typer(help="Versioned machine contract for coordinators.")
app.add_typer(contract_app, name="contract")


def _contract_print(payload: dict) -> None:
    import json as _json

    print(_json.dumps(payload, ensure_ascii=False))


def _contract_failure(reason: str) -> dict:
    return {
        "contractVersion": MEMORY_CONTRACT_VERSION,
        "success": False,
        "reason": reason,
    }


@contract_app.command("recall")
def contract_recall(
    query: str = typer.Argument(..., help="What to look for."),
    min_score: float = typer.Option(
        None,
        "--min-score",
        help=(
            "Relevance floor override. The default floor is tuned for precise "
            "human queries; a coordinator assembling an explicitly untrusted, "
            "budget-capped context digest may deliberately ask for more recall."
        ),
    ),
    repo: str = typer.Option(
        None, "--repo", help="Repository scope (required unless configured in the project)."
    ),
    endpoint: str = typer.Option(
        None, "--endpoint", help="Accepted for forward compatibility; local config wins."
    ),
    json_output: bool = typer.Option(True, "--json", help="Machine-readable output (always on)."),
):
    """Retrieve relevant memories as a contract-1.0 envelope."""
    del endpoint, json_output
    try:
        memory = get_memory()
        recall_kwargs = {} if min_score is None else {"min_score": max(0.0, min(1.0, min_score))}
        results = memory.recall(
            query, repo_id=_repo_scope(memory, repo), limit=10, **recall_kwargs
        )
    except Exception as exc:  # noqa: BLE001 - the contract reports, never crashes
        _contract_print(_contract_failure(f"recall failed: {exc}"))
        raise typer.Exit(1)

    entries = []
    for item in results:
        epistemic = item.get("epistemic_status")
        caveat = None
        if epistemic in {"hypothesized", "contradicted", "stale"}:
            caveat = f"epistemic status: {epistemic}"
        entries.append(
            {
                "kind": str(
                    item.get("belief_type")
                    or item.get("category")
                    or item.get("layer")
                    or "memory"
                ),
                "content": str(item.get("content", "")),
                **({"caveat": caveat} if caveat else {}),
            }
        )
    # The human CLI signposts matching intents on an empty recall; the machine
    # contract used to drop that signal, so the coordinator answered "there was
    # nothing to say" for the same query visp-memory itself pointed at the goal
    # layer. Additive and optional: an older consumer simply ignores it.
    intent_matches = 0
    if not entries:
        intent_matches = _intents_matching(memory, query, _repo_scope(memory, repo))

    _contract_print(
        {
            "contractVersion": MEMORY_CONTRACT_VERSION,
            "success": True,
            "entries": entries,
            **({"intentMatches": intent_matches} if intent_matches > 0 else {}),
        }
    )


@contract_app.command("propose")
def contract_propose(
    content: str = typer.Argument(..., help="What should be remembered."),
    repo: str = typer.Option(
        None, "--repo", help="Repository scope (required unless configured in the project)."
    ),
    endpoint: str = typer.Option(
        None, "--endpoint", help="Accepted for forward compatibility; local config wins."
    ),
    json_output: bool = typer.Option(True, "--json", help="Machine-readable output (always on)."),
):
    """Record a QUARANTINED proposal — durable only after the reviewed lifecycle accepts it."""
    del endpoint, json_output
    try:
        memory = get_memory()
        memory_id = memory.record(
            content,
            category="note",
            importance=0.5,
            repo_id=_repo_scope(memory, repo),
        )
        # The proposal enters the reviewed lifecycle, not active service:
        # recall serves status="active" only, so a quarantined row is invisible
        # until a human resolution activates it.
        #
        # ONLY status is set. Setting epistemic_status here was a defect that
        # bricked the whole store: `record` writes a non-semantic row, and the
        # storage integrity check refuses any non-semantic row carrying semantic
        # belief fields — so every SUBSEQUENT read of the database failed, not
        # just this one. Quarantine is a lifecycle state; epistemic status
        # belongs to the semantic layer and this row is not entitled to it.
        quarantined = memory._storage.update_memory(memory_id, status="quarantined")
        if not quarantined:
            _contract_print(
                _contract_failure(
                    "the proposal was stored but could not be quarantined; it was removed"
                )
            )
            memory._storage.delete_memory(memory_id)
            raise typer.Exit(1)
    except typer.Exit:
        raise
    except Exception as exc:  # noqa: BLE001 - the contract reports, never crashes
        _contract_print(_contract_failure(f"propose failed: {exc}"))
        raise typer.Exit(1)

    # Disclose the state rather than implying durability. The proposal is real
    # and stored, but NO command this CLI exposes can accept it, so `recall`
    # will never return it. Reporting a bare success with an id invited an
    # integrator to build a pipeline that stores nothing; saying so plainly is
    # the honest minimum until the acceptance path exists.
    _contract_print(
        {
            "contractVersion": MEMORY_CONTRACT_VERSION,
            "success": True,
            "proposalId": memory_id,
            "status": "quarantined",
            "durable": False,
            "note": (
                "Stored as a quarantined proposal. It is NOT retrievable by "
                "`contract recall`, which serves active memories only. No CLI "
                "command currently accepts a proposal, so this content stays "
                "invisible until the reviewed lifecycle is implemented."
            ),
        }
    )


def main():
    """Entry point."""
    app()


if __name__ == "__main__":
    main()
