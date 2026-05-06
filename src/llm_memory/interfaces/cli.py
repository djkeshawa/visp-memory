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
from rich.layout import Layout
from rich.console import Group

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
    data_dir: str = typer.Option(".llm-memory", "--data", "-d", help="Data directory"),
    repo: str = typer.Option(None, "--repo", "-r", help="Default repository/project ID")
):
    """Initialize LLM Memory in the current directory."""
    config_path = Path("llm-memory.yaml")

    if config_path.exists():
        console.print("[yellow]Config already exists. Use --force to overwrite.[/yellow]")
        raise typer.Exit(1)

    # Create config
    config = MemoryConfig(
        project_name=name or Path.cwd().name,
        project_type=project_type,
        repo_id=repo
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
    importance: float = typer.Option(0.5, "--importance", "-i", help="Importance (0.0-1.0)"),
    repo: str = typer.Option(None, "--repo", "-r", help="Repository context")
):
    """Record an episodic memory (something that happened)."""
    memory = get_memory()
    mem_id = memory.record(event, category=category, importance=importance, repo_id=repo)
    console.print(f"[green]Recorded:[/green] {event[:60]}...")
    console.print(f"[dim]ID: {mem_id}[/dim]")


@app.command()
def decision(
    what: str = typer.Argument(..., help="What was decided"),
    why: str = typer.Argument(..., help="Why this choice was made"),
    alternatives: List[str] = typer.Option(None, "--alt", "-a", help="Alternatives considered"),
    repo: str = typer.Option(None, "--repo", "-r", help="Repository context")
):
    """Record an architecture/design decision."""
    memory = get_memory()
    mem_id = memory.decision(what, why, alternatives, repo_id=repo)
    console.print(f"[green]Decision recorded:[/green] {what}")
    console.print(f"[dim]Reasoning: {why}[/dim]")


@app.command()
def bug(
    description: str = typer.Argument(..., help="Bug description"),
    cause: str = typer.Option(None, "--cause", "-c", help="Root cause"),
    fix: str = typer.Option(None, "--fix", "-f", help="How it was fixed"),
    files: List[str] = typer.Option(None, "--file", help="Files involved"),
    repo: str = typer.Option(None, "--repo", "-r", help="Repository context")
):
    """Record a bug discovery or fix."""
    memory = get_memory()
    mem_id = memory.episodic.bug(description, cause=cause, fix=fix, files=files, repo_id=repo)
    status = "fixed" if fix else "found"
    console.print(f"[green]Bug {status}:[/green] {description}")


# =============================================================================
# Learn Commands
# =============================================================================

@app.command()
def learn(
    knowledge: str = typer.Argument(..., help="The knowledge/fact/pattern"),
    category: str = typer.Option("fact", "--category", "-c", help="Knowledge category"),
    importance: float = typer.Option(0.6, "--importance", "-i", help="Importance (0.0-1.0)"),
    repo: str = typer.Option(None, "--repo", "-r", help="Repository context")
):
    """Establish semantic knowledge (something learned)."""
    memory = get_memory()
    # Note: Memory.learn does not currently accept repo_id override in its signature (it uses config),
    # but we should update it or temporarily set config.
    # Actually I fixed Memory.learn to use config.repo_id. Ideally I should update Memory.learn to ACSEPT an override too.
    # For now, let's inject it via config if passed.
    if repo:
        memory.config.repo_id = repo
    mem_id = memory.learn(knowledge, category=category, importance=importance)
    console.print(f"[green]Established:[/green] {knowledge[:60]}...")


@app.command()
def warn(
    area: str = typer.Argument(..., help="Area/file/module"),
    warning: str = typer.Argument(..., help="What to watch out for"),
    severity: float = typer.Option(0.7, "--severity", "-s", help="Severity (0.0-1.0)"),
    repo: str = typer.Option(None, "--repo", "-r", help="Repository context")
):
    """Add a warning about a fragile area."""
    memory = get_memory()
    if repo: memory.config.repo_id = repo
    mem_id = memory.warn(area, warning, severity)
    console.print(f"[yellow]Warning added for {area}:[/yellow] {warning}")


@app.command()
def convention(
    rule: str = typer.Argument(..., help="The convention/rule"),
    rationale: str = typer.Option(None, "--rationale", "-r", help="Why this convention exists"),
    repo: str = typer.Option(None, "--repo", "-rp", help="Repository context")
):
    """Establish a convention or best practice."""
    memory = get_memory()
    mem_id = memory.semantic.convention(rule, rationale, repo_id=repo)
    console.print(f"[green]Convention established:[/green] {rule}")


@app.command()
def issue(
    description: str = typer.Argument(..., help="Issue description"),
    workaround: str = typer.Option(None, "--workaround", "-w", help="How to work around it"),
    priority: float = typer.Option(0.5, "--priority", "-p", help="Priority (0.0-1.0)"),
    repo: str = typer.Option(None, "--repo", "-r", help="Repository context")
):
    """Document a known issue."""
    memory = get_memory()
    mem_id = memory.semantic.known_issue(description, workaround, priority, repo_id=repo)
    console.print(f"[yellow]Known issue documented:[/yellow] {description}")


# =============================================================================
# Intent Commands
# =============================================================================

@app.command()
def goal(
    description: str = typer.Argument(..., help="Goal description"),
    priority: int = typer.Option(1, "--priority", "-p", help="Priority (0=low, 1=normal, 2=high, 3=critical)"),
    constraint: List[str] = typer.Option(None, "--constraint", "-c", help="Constraints"),
    repo: str = typer.Option(None, "--repo", "-r", help="Repository context")
):
    """Set a goal/intent."""
    memory = get_memory()
    if repo: memory.config.repo_id = repo
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
    files: List[str] = typer.Option(None, "--file", "-f", help="Files being modified"),
    repo: str = typer.Option(None, "--repo", "-r", help="Repository context")
):
    """Set current task."""
    memory = get_memory()
    if repo: memory.config.repo_id = repo
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
    layer: str = typer.Option(None, "--layer", "-l", help="Filter by layer"),
    repo: str = typer.Option(None, "--repo", "-r", help="Filter by repository")
):
    """Search across all memories."""
    memory = get_memory()

    layers = [layer] if layer else None
    results = memory.recall(query, layers=layers, limit=limit, repo_id=repo)

    if not results:
        console.print("[yellow]No results found[/yellow]")
        return

    from rich.box import ROUNDED
    table = Table(title=f"Search Results for '{query}'", box=ROUNDED)
    table.add_column("Layer", style="cyan", width=10)
    table.add_column("Category", style="green", width=12)
    table.add_column("Content")
    table.add_column("Score", justify="right", style="magenta")

    for r in results:
        score = f"{r.get('similarity', 0):.2f}" if r.get('similarity') else "-"
        content = r['content'].replace("\n", " ")
        if len(content) > 80:
            content = content[:77] + "..."

        table.add_row(
            r['layer'],
            r.get('category', '-'),
            content,
            score
        )

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
def dedup(
    layer: str = typer.Option("episodic", "--layer", "-l", help="Layer to check"),
    threshold: float = typer.Option(0.9, "--threshold", "-t", help="Similarity threshold")
):
    """Find and merge duplicate memories."""

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
            console.print(f"[{mem['id']}] {mem['content'][:50]}... ({mem.get('similarity', 0):.2f})")

    # TODO: Interactive merge workflow could be added here


# =============================================================================
# Quality Commands
# =============================================================================

quality_app = typer.Typer(help="Memory quality management")
app.add_typer(quality_app, name="quality")

@quality_app.command("conflicts")
def check_conflicts(
    content: str = typer.Argument(..., help="Statement to check for conflicts"),
    layer: str = typer.Option("semantic", "--layer", "-l", help="Layer to check against")
):
    """Check if a statement conflicts with existing knowledge."""
    memory = get_memory()
    
    console.print(f"Checking for conflicts with: '{content}'")
    
    try:
        conflict = memory.check_conflict(content, layer=layer)
        
        if conflict:
            console.print(Panel(f"[bold red]Conflict Detected![/bold red]"))
            console.print(f"Reason: {conflict.get('reason')}")
            if conflict.get('conflicting_ids'):
                console.print(f"Conflicting IDs: {conflict.get('conflicting_ids')}")
        else:
            console.print("[green]No conflicts detected.[/green]")
    except Exception as e:
        console.print(f"[red]Error checking conflicts:[/red] {e}")


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

@app.command("list")
def list_memories(
    layer: str = typer.Option(None, "--layer", "-l", help="Filter by layer"),
    category: str = typer.Option(None, "--category", "-c", help="Filter by category"),
    limit: int = typer.Option(20, "--limit", "-n", help="Max results"),
    full: bool = typer.Option(False, "--full", help="Show full content")
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
        order_by="created_at DESC"
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
        content = m['content'].replace("\n", " ")
        if not full and len(content) > 80:
            content = content[:77] + "..."

        table.add_row(
            m['id'][:8],
            m['created_at'][:16].replace("T", " "),
            m['layer'],
            m.get('category', '-'),
            content
        )

    console.print(table)


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
# Recall Commands (Proactive)
# =============================================================================

@app.command()
def inject(
    files: List[str] = typer.Option(None, "--file", "-f", help="Files to get context for"),
    task: str = typer.Option(None, "--task", "-t", help="Task description"),
    format: str = typer.Option("markdown", "--format", help="Output format: markdown, plain, json"),
    max_length: int = typer.Option(2000, "--max-length", "-m", help="Max output length")
):
    """
    Inject relevant memory context for files or task.

    This command surfaces warnings, bugs, decisions, and knowledge
    relevant to specific files or tasks without being asked.
    """
    from llm_memory.recall.proactive import ProactiveRecall

    memory = get_memory()
    recall = ProactiveRecall(memory)

    if files and task:
        # Comprehensive context for task with files
        context = recall.find_relevant_for_task(
            task_description=task,
            files=files
        )
        output = recall.format_injection(context, format=format, max_length=max_length)

    elif files:
        # File-specific context
        if len(files) == 1:
            context = recall.on_file_open(files[0])
        else:
            # Multiple files - aggregate
            context = {
                "warnings": [],
                "bugs": [],
                "decisions": [],
                "knowledge": []
            }
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
    limit: int = typer.Option(5, "--limit", "-n", help="Max results")
):
    """
    Find similar errors that occurred in the past.

    Searches for similar error messages along with their fixes
    and workarounds.
    """
    from llm_memory.recall.proactive import ProactiveRecall

    memory = get_memory()
    recall = ProactiveRecall(memory)

    similar = recall.on_error(
        error_message=error,
        error_type=error_type,
        file_path=file,
        limit=limit
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
    commit_ref: str = typer.Option("HEAD", "--ref", "-r", help="Commit reference for 'commit' action"),
    since: str = typer.Option(None, "--since", "-s", help="Date for 'sync' (e.g., '1 week ago')"),
    limit: int = typer.Option(100, "--limit", "-n", help="Max commits for 'sync'")
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
        from llm_memory.capture.git import GitCapture
    except ImportError:
        console.print("[red]Git capture not available.[/red]")
        console.print("Install with: [bold]pip install llm-memory[capture][/bold]")
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
                console.print(f"[yellow]✗[/yellow] Could not remove {hook} (not installed by llm-memory)")

        console.print("\n[green]Git hooks removed.[/green]")

    elif action == "sync":
        console.print(f"Syncing git history (limit: {limit})...")

        memory_ids = git_capture.sync_history(since=since, limit=limit)

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
def capture_tests(
    report: str = typer.Argument("report.xml", help="Path to JUnit XML report")
):
    """Capture test failures from JUnit XML report."""
    from llm_memory.capture.tests import TestCapture

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
    team_id: str = typer.Option(None, "--team", "-t", help="Owning team ID")
):
    """Register a new repository."""
    memory = get_memory()
    from llm_memory.core.repository import Repository
    
    repo = Repository(
        id=name,  # Use name as ID for simplicity in CLI
        name=name,
        url=url,
        description=description,
        team_id=team_id
    )
    
    repo_id = memory.repos.register(repo)
    console.print(f"[green]Registered repository:[/green] {name}")
    console.print(f"[dim]ID: {repo_id}[/dim]")


@repo_app.command("list")
def list_repos(
    team: str = typer.Option(None, "--team", "-t", help="Filter by team ID")
):
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
        table.add_row(
            r.id,
            r.name,
            r.team_id or "-",
            r.description or ""
        )
        
    console.print(table)


@repo_app.command("dependency")
def add_dependency(
    source: str = typer.Argument(..., help="Source repository ID"),
    target: str = typer.Argument(..., help="Target repository ID"),
    type: str = typer.Option("depends_on", "--type", "-t", help="Dependency type")
):
    """Add a dependency between repositories."""
    memory = get_memory()
    from llm_memory.core.repository import DependencyType, RepositoryDependency

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


@repo_app.command("context")
def repo_context(
    repo: str = typer.Argument(..., help="Repository ID"),
    format: str = typer.Option("text", "--format", "-f", help="Output format (text/json)")
):
    """Get cross-repository context (warnings from dependencies)."""
    memory = get_memory()
    from llm_memory.core.cross_repo import CrossRepoContext
    
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


@team_app.command("create")
def create_team(
    name: str = typer.Argument(..., help="Team name"),
    description: str = typer.Option(None, "--desc", "-d", help="Description")
):
    """Create a new team."""
    memory = get_memory()
    from llm_memory.core.team import Team
    
    team = Team(
        id=name.lower().replace(" ", "-"),
        name=name,
        description=description
    )
    
    team_id = memory.teams.create_team(team)
    console.print(f"[green]Created team:[/green] {name}")
    console.print(f"[dim]ID: {team_id}[/dim]")


@team_app.command("user")
def create_user(
    username: str = typer.Argument(..., help="Username"),
    email: str = typer.Option(None, "--email", "-e", help="Email address"),
    name: str = typer.Option(None, "--name", "-n", help="Display name")
):
    """Create or update a user."""
    memory = get_memory()
    from llm_memory.core.team import User
    
    user = User(
        id=username,
        username=username,
        email=email,
        display_name=name or username
    )
    
    user_id = memory.teams.create_user(user)
    console.print(f"[green]User saved:[/green] {username}")


@team_app.command("add-member")
def add_team_member(
    team: str = typer.Argument(..., help="Team ID"),
    user: str = typer.Argument(..., help="User ID/Username")
):
    """Add a user to a team."""
    memory = get_memory()
    
    if memory.teams.add_member(team, user):
        console.print(f"[green]Added {user} to team {team}[/green]")
    else:
        console.print(f"[red]Failed to add member (check IDs)[/red]")


@team_app.command("list")
def list_user_teams(
    user: str = typer.Argument("current", help="User ID (defaults to current if authenticated)")
):
    """List teams for a user."""
    memory = get_memory()
    
    if user == "current":
        # Check if we have a config user (only in authenticated contexts)
        # For local, this might be ambiguous. Let's warn.
        console.print("[yellow]Please provide specific user ID for local mode[/yellow]")
        return

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
    dry_run: bool = typer.Option(False, "--dry-run", help="Analyze without saving")
):
    """
    Capture memories from a conversation log.
    
    Uses configured LLM to extract decisions, learnings, bugs, and tasks.
    """
    try:
        from llm_memory.capture.conversation import ConversationCapture
    except ImportError:
        console.print("[red]Import Error: Could not load ConversationCapture[/red]")
        raise typer.Exit(1)
        
    memory = get_memory()
    
    try:
        capturer = ConversationCapture(memory)
        console.print(f"Analyzing {file}...")
        
        result = capturer.parse_file(file, dry_run=dry_run)
        
        console.print(Panel(f"[bold]Extraction Results ({'Dry Run' if dry_run else 'Saved'})[/bold]"))
        console.print(f"Decisions: {result['decisions']}")
        console.print(f"Learnings: {result['learnings']}")
        console.print(f"Bugs:      {result['bugs']}")
        console.print(f"Tasks:     {result['tasks']}")
        
        if dry_run and result['raw']:
            console.print("\n[dim]Raw Extraction:[/dim]")
            console.print_json(data=result['raw'])
            
    except ValueError as e:
        console.print(f"[red]Configuration Error:[/red] {e}")
        console.print("Ensure LLM_MEMORY_CAPTURE_LLM_PROVIDER is set.")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)



# =============================================================================
# Hooks Commands (LLM Tool Integration)
# =============================================================================

# Create hooks sub-app
hooks_app = typer.Typer(help="Integration with LLM tools (Claude Code, Cursor, Aider)")
app.add_typer(hooks_app, name="hooks")


@hooks_app.command("install")
def hooks_install(
    tool: str = typer.Argument(..., help="Tool name: claude-code, cursor, aider, generic")
):
    """
    Install hooks for an LLM tool.

    Installs context injection for the specified tool.
    """
    from llm_memory.hooks import get_adapter

    memory = get_memory()

    try:
        adapter = get_adapter(tool, memory=memory)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    console.print(f"Installing {tool} integration...")

    results = adapter.install()

    for component, success in results.items():
        if success:
            console.print(f"[green]✓[/green] {component}")
        else:
            console.print(f"[yellow]✗[/yellow] {component}")

    console.print(f"\n[green]{tool} integration installed![/green]")
    console.print(f"Context file: {adapter.get_context_file_path()}")
    console.print(f"\nUpdate context with: [bold]llm-memory hooks update {tool}[/bold]")


@hooks_app.command("uninstall")
def hooks_uninstall(
    tool: str = typer.Argument(..., help="Tool name: claude-code, cursor, aider, generic")
):
    """Remove hooks for an LLM tool."""
    from llm_memory.hooks import get_adapter

    memory = get_memory()

    try:
        adapter = get_adapter(tool, memory=memory)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    console.print(f"Uninstalling {tool} integration...")

    results = adapter.uninstall()

    for component, success in results.items():
        if success:
            console.print(f"[green]✓[/green] {component} removed")
        else:
            console.print(f"[yellow]✗[/yellow] {component} not found")

    console.print(f"\n[green]{tool} integration removed.[/green]")


@hooks_app.command("update")
def hooks_update(
    tool: str = typer.Argument(..., help="Tool name: claude-code, cursor, aider, generic"),
    files: List[str] = typer.Option(None, "--file", "-f", help="Files being worked on"),
    task: str = typer.Option(None, "--task", "-t", help="Task description")
):
    """
    Update context for an LLM tool.

    Refreshes the tool's context file with current memory.
    """
    from llm_memory.hooks import get_adapter

    memory = get_memory()

    try:
        adapter = get_adapter(tool, memory=memory)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    if not adapter.is_installed():
        console.print(f"[yellow]{tool} integration not installed.[/yellow]")
        console.print(f"Install with: [bold]llm-memory hooks install {tool}[/bold]")
        raise typer.Exit(1)

    console.print(f"Updating {tool} context...")

    success = adapter.update_context(files=files, task=task)

    if success:
        console.print("[green]✓ Context updated[/green]")
        console.print(f"File: {adapter.get_context_file_path()}")
    else:
        console.print("[red]✗ Failed to update context[/red]")


@hooks_app.command("list")
def hooks_list():
    """List available LLM tool integrations."""
    console.print(Panel("[bold]Available LLM Tool Integrations[/bold]"))

    tools = [
        ("claude-code", "Claude Code / Claude Desktop", "CLAUDE.md"),
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

    console.print("\n[dim]Install with:[/dim] [bold]llm-memory hooks install <tool>[/bold]")


# =============================================================================
# MCP Server Command
# =============================================================================

@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host", "-h", help="Host to bind"),
    port: int = typer.Option(8000, "--port", "-p", help="Port to bind"),
    reload: bool = typer.Option(False, "--reload", help="Enable auto-reload")
):
    """Run the MCP or Central Memory Server."""
    # Note: Currently this command is ambiguous between MCP and FastAPI
    # Once we switch to client-server, this will run the FastAPI server
    # For now, let's make it run the FastAPI skeleton if requested, or MCP by default?
    # Actually, let's keep it specific.

    console.print(f"[green]Starting Central Memory Server at http://{host}:{port}[/green]")
    try:
        import uvicorn
        uvicorn.run("llm_memory.server.app:app", host=host, port=port, reload=reload)
    except ImportError:
        console.print("[red]uvicorn not installed.[/red]")
        console.print("Install with: [bold]pip install llm-memory[api][/bold]")
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
    recent = memory.episodic.recent(limit=5)
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
    intent_summary = memory.intent.summarize()

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
            Text.from_markup(task_text)
        ),
        title="Active Context",
        box=ROUNDED
    )

    # Layout Construction
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="main", ratio=1),
        Layout(name="footer", size=3)
    )

    layout["header"].update(
        Panel(
            Align.center(f"[bold blue]LLM Memory System[/bold blue] - {memory.config.storage.data_dir}"),
            box=ROUNDED,
            style="white on black"
        )
    )

    layout["main"].split_row(
        Layout(name="left", ratio=1),
        Layout(name="right", ratio=2)
    )

    layout["left"].split_column(
        Layout(name="context", ratio=1),
        Layout(name="stats", ratio=1)
    )

    layout["left"]["context"].update(context_panel)
    layout["left"]["stats"].update(layer_table)

    layout["right"].update(activity_table)

    layout["footer"].update(
        Align.center("[dim]Run 'llm-memory help' for commands | 'llm-memory recall' to search[/dim]")
    )

    console.print(layout)


def main():
    """Entry point."""
    app()


if __name__ == "__main__":
    main()
