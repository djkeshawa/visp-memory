"""
Command Line Interface for LLM Memory.

Usage:
    llm-memory init                    # Initialize in current directory
    llm-memory record "Fixed bug"      # Record an event
    llm-memory decision "..." "..."    # Record a decision
    llm-memory learn "..."             # Establish knowledge
    llm-memory warn "area" "warning"   # Add a warning
    llm-memory goal "..."              # Set a goal
    llm-memory working "task"          # Set current task
    llm-memory done                    # Clear current task
    llm-memory recall "query"          # Search memories
    llm-memory context                 # Get full context
    llm-memory stats                   # Show statistics
"""

from pathlib import Path
from typing import Optional, List
import json

import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.markdown import Markdown

from llm_memory import Memory, MemoryConfig

app = typer.Typer(
    name="llm-memory",
    help="Human-inspired memory system for LLMs",
    no_args_is_help=True
)
console = Console()

# Global memory instance (lazy loaded)
_memory: Optional[Memory] = None


def get_memory() -> Memory:
    """Get or create memory instance."""
    global _memory
    if _memory is None:
        _memory = Memory()
    return _memory


# =============================================================================
# Init Command
# =============================================================================

@app.command()
def init(
    project_type: str = typer.Option("code", "--type", "-t", help="Project type: code, writing, research, general"),
    name: str = typer.Option(None, "--name", "-n", help="Project name"),
    data_dir: str = typer.Option(".llm-memory", "--data", "-d", help="Data directory")
):
    """Initialize LLM Memory in the current directory."""
    config_path = Path("llm-memory.yaml")

    if config_path.exists():
        console.print("[yellow]Config already exists. Use --force to overwrite.[/yellow]")
        raise typer.Exit(1)

    # Create config
    config = MemoryConfig(
        project_name=name or Path.cwd().name,
        project_type=project_type
    )
    config.storage.data_dir = Path(data_dir) / "data"

    # Create data directory
    config.storage.data_dir.mkdir(parents=True, exist_ok=True)

    # Save config using the save method (handles Path conversion)
    config.save(config_path)

    # Initialize memory to create tables
    Memory(config=config)

    console.print(f"[green]Initialized LLM Memory in {config_path}[/green]")
    console.print(f"Data directory: {config.storage.data_dir}")


# =============================================================================
# Record Commands
# =============================================================================

@app.command()
def record(
    event: str = typer.Argument(..., help="What happened"),
    category: str = typer.Option("note", "--category", "-c", help="Event category"),
    importance: float = typer.Option(0.5, "--importance", "-i", help="Importance (0.0-1.0)")
):
    """Record an episodic memory (something that happened)."""
    memory = get_memory()
    mem_id = memory.record(event, category=category, importance=importance)
    console.print(f"[green]Recorded:[/green] {event[:60]}...")
    console.print(f"[dim]ID: {mem_id}[/dim]")


@app.command()
def decision(
    what: str = typer.Argument(..., help="What was decided"),
    why: str = typer.Argument(..., help="Why this choice was made"),
    alternatives: List[str] = typer.Option(None, "--alt", "-a", help="Alternatives considered")
):
    """Record an architecture/design decision."""
    memory = get_memory()
    mem_id = memory.decision(what, why, alternatives)
    console.print(f"[green]Decision recorded:[/green] {what}")
    console.print(f"[dim]Reasoning: {why}[/dim]")


@app.command()
def bug(
    description: str = typer.Argument(..., help="Bug description"),
    cause: str = typer.Option(None, "--cause", "-c", help="Root cause"),
    fix: str = typer.Option(None, "--fix", "-f", help="How it was fixed"),
    files: List[str] = typer.Option(None, "--file", help="Files involved")
):
    """Record a bug discovery or fix."""
    memory = get_memory()
    mem_id = memory.episodic.bug(description, cause=cause, fix=fix, files=files)
    status = "fixed" if fix else "found"
    console.print(f"[green]Bug {status}:[/green] {description}")


# =============================================================================
# Learn Commands
# =============================================================================

@app.command()
def learn(
    knowledge: str = typer.Argument(..., help="The knowledge/fact/pattern"),
    category: str = typer.Option("fact", "--category", "-c", help="Knowledge category"),
    importance: float = typer.Option(0.6, "--importance", "-i", help="Importance (0.0-1.0)")
):
    """Establish semantic knowledge (something learned)."""
    memory = get_memory()
    mem_id = memory.learn(knowledge, category=category, importance=importance)
    console.print(f"[green]Established:[/green] {knowledge[:60]}...")


@app.command()
def warn(
    area: str = typer.Argument(..., help="Area/file/module"),
    warning: str = typer.Argument(..., help="What to watch out for"),
    severity: float = typer.Option(0.7, "--severity", "-s", help="Severity (0.0-1.0)")
):
    """Add a warning about a fragile area."""
    memory = get_memory()
    mem_id = memory.warn(area, warning, severity)
    console.print(f"[yellow]Warning added for {area}:[/yellow] {warning}")


@app.command()
def convention(
    rule: str = typer.Argument(..., help="The convention/rule"),
    rationale: str = typer.Option(None, "--rationale", "-r", help="Why this convention exists")
):
    """Establish a convention or best practice."""
    memory = get_memory()
    mem_id = memory.semantic.convention(rule, rationale)
    console.print(f"[green]Convention established:[/green] {rule}")


@app.command()
def issue(
    description: str = typer.Argument(..., help="Issue description"),
    workaround: str = typer.Option(None, "--workaround", "-w", help="How to work around it"),
    priority: float = typer.Option(0.5, "--priority", "-p", help="Priority (0.0-1.0)")
):
    """Document a known issue."""
    memory = get_memory()
    mem_id = memory.semantic.known_issue(description, workaround, priority)
    console.print(f"[yellow]Known issue documented:[/yellow] {description}")


# =============================================================================
# Intent Commands
# =============================================================================

@app.command()
def goal(
    description: str = typer.Argument(..., help="Goal description"),
    priority: int = typer.Option(1, "--priority", "-p", help="Priority (0=low, 1=normal, 2=high, 3=critical)"),
    constraint: List[str] = typer.Option(None, "--constraint", "-c", help="Constraints")
):
    """Set a goal/intent."""
    memory = get_memory()
    intent_id = memory.goal(description, priority=priority, constraints=constraint)
    console.print(f"[green]Goal set:[/green] {description}")


@app.command()
def focus(
    on: str = typer.Argument(..., help="What to focus on"),
    avoid: List[str] = typer.Option(None, "--avoid", "-a", help="What to avoid")
):
    """Set current focus with things to avoid."""
    memory = get_memory()
    intent_id = memory.intent.set_focus(on, avoid)
    console.print(f"[green]Focus set:[/green] {on}")
    if avoid:
        console.print(f"[dim]Avoiding: {', '.join(avoid)}[/dim]")


@app.command()
def working(
    task: str = typer.Argument(..., help="What you're working on"),
    files: List[str] = typer.Option(None, "--file", "-f", help="Files being modified")
):
    """Set current task."""
    memory = get_memory()
    intent_id = memory.working_on(task, files)
    console.print(f"[green]Working on:[/green] {task}")


@app.command()
def done():
    """Clear current task (mark as done)."""
    memory = get_memory()
    cleared = memory.done()
    console.print(f"[green]Cleared {cleared} task(s)[/green]")


# =============================================================================
# Search Commands
# =============================================================================

@app.command()
def recall(
    query: str = typer.Argument(..., help="Search query"),
    limit: int = typer.Option(10, "--limit", "-n", help="Maximum results"),
    layer: str = typer.Option(None, "--layer", "-l", help="Filter by layer")
):
    """Search across all memories."""
    memory = get_memory()

    layers = [layer] if layer else None
    results = memory.recall(query, layers=layers, limit=limit)

    if not results:
        console.print("[yellow]No results found[/yellow]")
        return

    table = Table(title=f"Search Results for '{query}'")
    table.add_column("Layer", style="cyan")
    table.add_column("Category", style="green")
    table.add_column("Content")
    table.add_column("Score", justify="right")

    for r in results:
        score = f"{r.get('similarity', 0):.2f}" if r.get('similarity') else "-"
        content = r['content'][:60] + "..." if len(r['content']) > 60 else r['content']
        table.add_row(r['layer'], r.get('category', '-'), content, score)

    console.print(table)


# =============================================================================
# Context Commands
# =============================================================================

@app.command()
def context(
    format: str = typer.Option("text", "--format", "-f", help="Output format: text or json"),
    no_history: bool = typer.Option(False, "--no-history", help="Exclude history"),
    no_knowledge: bool = typer.Option(False, "--no-knowledge", help="Exclude knowledge"),
    no_intent: bool = typer.Option(False, "--no-intent", help="Exclude intent")
):
    """Get full context for LLM injection."""
    memory = get_memory()

    ctx = memory.context(
        include_history=not no_history,
        include_knowledge=not no_knowledge,
        include_intent=not no_intent,
        format=format
    )

    if format == "json":
        console.print_json(json.dumps(ctx, default=str))
    else:
        console.print(Markdown(ctx))


@app.command()
def relevant(
    task: str = typer.Option(None, "--task", "-t", help="Task description"),
    files: List[str] = typer.Option(None, "--file", "-f", help="Files being worked on")
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

    table.add_row("Total Memories", str(s.get('total_memories', 0)))
    table.add_row("Active Intents", str(s.get('active_intents', 0)))
    table.add_row("Relationships", str(s.get('total_relationships', 0)))

    console.print(table)

    if s.get('memories_by_layer'):
        console.print("\n[bold]By Layer:[/bold]")
        for layer, count in s['memories_by_layer'].items():
            console.print(f"  {layer}: {count}")

    if s.get('memories_by_category'):
        console.print("\n[bold]By Category:[/bold]")
        for cat, count in list(s['memories_by_category'].items())[:10]:
            console.print(f"  {cat}: {count}")


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


@app.command()
def export(
    output: str = typer.Argument("memory-export.json", help="Output file path")
):
    """Export all memories to JSON."""
    memory = get_memory()
    memory.export(Path(output))
    console.print(f"[green]Exported to {output}[/green]")


@app.command(name="import")
def import_memories(
    input_file: str = typer.Argument(..., help="Input file path")
):
    """Import memories from JSON export."""
    memory = get_memory()
    memory.import_memories(Path(input_file))
    console.print(f"[green]Imported from {input_file}[/green]")


# =============================================================================
# List Commands
# =============================================================================

@app.command()
def list_intents():
    """List active intents/goals."""
    memory = get_memory()
    intents = memory.intent.get_active()

    if not intents:
        console.print("[yellow]No active intents[/yellow]")
        return

    table = Table(title="Active Intents")
    table.add_column("Priority", style="cyan", justify="center")
    table.add_column("Description")

    priority_labels = {0: "LOW", 1: "NORMAL", 2: "HIGH", 3: "CRITICAL"}

    for i in intents:
        p = priority_labels.get(i.get('priority', 1), str(i.get('priority')))
        table.add_row(p, i['description'])

    console.print(table)


@app.command()
def list_warnings():
    """List all warnings."""
    memory = get_memory()
    warnings = memory.semantic.get_warnings()

    if not warnings:
        console.print("[green]No warnings[/green]")
        return

    console.print(Panel("[bold yellow]Warnings[/bold yellow]"))
    for w in warnings:
        console.print(f"  - {w['content']}")


@app.command()
def list_issues():
    """List known issues."""
    memory = get_memory()
    issues = memory.semantic.get_known_issues()

    if not issues:
        console.print("[green]No known issues[/green]")
        return

    console.print(Panel("[bold]Known Issues[/bold]"))
    for i in issues:
        console.print(f"  - {i['content']}")


# =============================================================================
# MCP Server Command
# =============================================================================

@app.command()
def serve():
    """Run the MCP server for LLM tool integration."""
    try:
        from llm_memory.interfaces.mcp import main as mcp_main
        mcp_main()
    except ImportError as e:
        console.print("[red]MCP package not installed.[/red]")
        console.print("Install with: [bold]pip install llm-memory[mcp][/bold]")
        raise typer.Exit(1)


def main():
    """Entry point."""
    app()


if __name__ == "__main__":
    main()
