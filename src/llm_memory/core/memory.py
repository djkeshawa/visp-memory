"""
Main Memory System

The unified interface for LLM memory, bringing together:
- Episodic memories (events)
- Semantic memories (knowledge)
- Intent (goals/direction)
- Compression (memory consolidation)
"""

from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime
import json

from llm_memory.config import MemoryConfig
from llm_memory.core.storage import LocalStorage
from llm_memory.core.remote_storage import RemoteStorage
from llm_memory.core.neo4j_storage import Neo4jStorage
from llm_memory.core.compression import MemoryCompressor, create_llm_compressor
from llm_memory.layers.episodic import EpisodicMemory, EpisodeCategory
from llm_memory.layers.semantic import SemanticMemory, KnowledgeCategory
from llm_memory.layers.intent import IntentMemory, IntentPriority
from llm_memory.quality.dedup import Deduplicator


class Memory:
    """
    Human-inspired memory system for LLMs.

    Provides persistent context without re-analyzing everything each session.

    Usage:
        memory = Memory()  # Auto-discovers config
        memory = Memory(config=MemoryConfig(...))  # Explicit config
        memory = Memory(data_dir=Path("./data"))  # Quick setup

    Layers:
        memory.episodic  - Event-based memories (what happened)
        memory.semantic  - Knowledge (what we learned)
        memory.intent    - Goals and direction (where we're going)

    Quick methods:
        memory.record(...)   - Record an event
        memory.learn(...)    - Establish knowledge
        memory.goal(...)     - Set a goal
        memory.recall(...)   - Search all memories
        memory.context()     - Get full context for LLM
    """

    def __init__(
        self,
        config: MemoryConfig = None,
        data_dir: Path = None
    ):
        """
        Initialize memory system.

        Args:
            config: Full configuration (if None, auto-discovers)
            data_dir: Quick setup with just a data directory
        """
        if config:
            self.config = config
        elif data_dir:
            self.config = MemoryConfig()
            self.config.storage.data_dir = Path(data_dir)
        else:
            self.config = MemoryConfig.find_and_load()

        # Initialize embedding provider
        embedding_fn = None
        try:
            from llm_memory.core.embeddings import get_embedding_provider
            embedder = get_embedding_provider(self.config.embedding)
            embedding_fn = embedder.embed
        except Exception as e:
            # Embedding provider initialization failed - will use fallback search
            import logging
            logging.warning(f"Failed to initialize embedding provider: {e}")

        # Initialize storage
        if self.config.storage.mode == "client":
            self._storage = RemoteStorage(
                server_url=self.config.storage.server_url,
                api_key=self.config.storage.api_key
            )
        elif self.config.storage.backend == "neo4j":
            self._storage = Neo4jStorage(
                uri=self.config.storage.neo4j_uri,
                user=self.config.storage.neo4j_user,
                password=self.config.storage.neo4j_password,
                embedding_fn=embedding_fn
            )
        else:
            self._storage = LocalStorage(self.config.storage.data_dir, embedding_fn=embedding_fn)

        # Initialize layers
        self.episodic = EpisodicMemory(self._storage)
        self.semantic = SemanticMemory(self._storage)
        self.intent = IntentMemory(self._storage)

        # Initialize compressor
        compress_fn = None
        if self.config.compression.llm_provider:
            try:
                compress_fn = create_llm_compressor(
                    provider=self.config.compression.llm_provider,
                    model=self.config.compression.llm_model
                )
            except Exception:
                pass  # Fall back to heuristic compression

        self._compressor = MemoryCompressor(self._storage, compress_fn)
        self.deduplicator = Deduplicator(self._storage)

    # =========================================================================
    # Quick Access Methods
    # =========================================================================

    def record(
        self,
        event: str,
        category: str = "note",
        importance: float = 0.5,
        **kwargs
    ) -> str:
        """
        Quick method to record an episodic memory.

        Args:
            event: What happened
            category: Type of event (decision, bug, discovery, etc.)
            importance: How important (0.0 to 1.0)
            **kwargs: Additional arguments passed to episodic.record

        Returns:
            Memory ID
        """
        try:
            cat = EpisodeCategory(category)
        except ValueError:
            cat = category

        # Use configured repo_id if not provided
        if kwargs.get("repo_id") is None and self.config.repo_id:
            kwargs["repo_id"] = self.config.repo_id

        return self.episodic.record(
            content=event,
            category=cat,
            importance=importance,
            **kwargs
        )

    def decision(
        self,
        what: str,
        why: str,
        alternatives: List[str] = None,
        repo_id: str = None
    ) -> str:
        """
        Quick method to record a decision.

        Args:
            what: What was decided
            why: Why this choice was made
            alternatives: What alternatives were considered
            repo_id: Optional repository context (defaults to config.repo_id)

        Returns:
            Memory ID
        """
        return self.episodic.decision(
            what,
            why,
            alternatives,
            repo_id=repo_id or self.config.repo_id
        )

    def learn(
        self,
        knowledge: str,
        category: str = "fact",
        importance: float = 0.6,
        repo_id: str = None
    ) -> str:
        """
        Quick method to establish semantic knowledge.

        Args:
            knowledge: The knowledge/fact/pattern
            category: Type (invariant, pattern, convention, etc.)
            importance: How important
            repo_id: Optional repository context

        Returns:
            Memory ID
        """
        try:
            cat = KnowledgeCategory(category)
        except ValueError:
            cat = category

        return self.semantic.establish(
            knowledge=knowledge,
            category=cat,
            importance=importance,
            repo_id=repo_id or self.config.repo_id
        )

    def warn(self, area: str, warning: str, severity: float = 0.7, repo_id: str = None) -> str:
        """
        Quick method to establish a warning.

        Args:
            area: What area
            warning: What to watch out for
            severity: How serious
            repo_id: Optional repository context

        Returns:
            Memory ID
        """
        return self.semantic.warn(area, warning, severity, repo_id=repo_id or self.config.repo_id)

    def goal(
        self,
        goal: str,
        priority: int = 1,
        constraints: List[str] = None,
        repo_id: str = None
    ) -> str:
        """
        Quick method to set a goal.

        Args:
            goal: The goal description
            priority: 0=low, 1=normal, 2=high, 3=critical
            constraints: Constraints to respect
            repo_id: Optional repository context

        Returns:
            Intent ID
        """
        return self.intent.set_goal(
            goal=goal,
            priority=IntentPriority(priority),
            constraints=constraints,
            repo_id=repo_id or self.config.repo_id
        )

    def working_on(self, task: str, files: List[str] = None, repo_id: str = None) -> str:
        """
        Quick method to set current task.

        Args:
            task: What you're working on
            files: Files being modified
            repo_id: Optional repository context

        Returns:
            Intent ID
        """
        return self.intent.working_on(task, files, repo_id=repo_id or self.config.repo_id)

    def done(self) -> int:
        """Clear current task (mark as done)."""
        return self.intent.clear_task()

    # =========================================================================
    # Search and Recall
    # =========================================================================

    def recall(
        self,
        query: str,
        layers: List[str] = None,
        repo_id: str = None,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Search across all memory layers.

        Args:
            query: Search query (natural language)
            layers: Which layers to search (default: all)
            limit: Maximum results per layer

        Returns:
            List of matching memories with similarity scores
        """
        layers = layers or ["episodic", "semantic", "intent"]
        results = []

        # Use configured repo_id if not provided
        search_repo_id = repo_id or self.config.repo_id

        for layer in layers:
            layer_results = self._storage.search_memories(
                query=query,
                layer=layer if layer != "intent" else None,
                repo_id=search_repo_id,
                limit=limit
            )
            results.extend(layer_results)

        # Sort by similarity
        results.sort(key=lambda x: x.get("similarity", 0), reverse=True)
        return results[:limit]

    def relevant_for(
        self,
        task: str = None,
        files: List[str] = None,
        limit: int = 15
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Get memories relevant to a task or set of files.

        Args:
            task: Task description
            files: Files being worked on
            limit: Maximum results per category

        Returns:
            Dict with 'knowledge', 'warnings', 'history' keys
        """
        results = {
            "knowledge": [],
            "warnings": [],
            "history": []
        }

        # Get relevant semantic knowledge
        if task or files:
            results["knowledge"] = self.semantic.relevant_for(
                files=files,
                query=task,
                limit=limit
            )

        # Get warnings for files
        if files:
            for f in files:
                warnings = self.semantic.get_warnings(f)
                results["warnings"].extend(warnings)

        # Get relevant history
        if task:
            results["history"] = self.episodic.search(task, limit=limit // 2)

        return results

    # =========================================================================
    # Context Generation
    # =========================================================================

    def context(
        self,
        include_history: bool = True,
        include_knowledge: bool = True,
        include_intent: bool = True,
        format: str = "text"
    ) -> Any:
        """
        Generate full context for an LLM.

        This is the main method for getting memory context to inject
        into an LLM prompt.

        Args:
            include_history: Include recent episodic memories
            include_knowledge: Include semantic knowledge
            include_intent: Include current intents/goals
            format: "text" for human-readable, "json" for structured

        Returns:
            Context string or dict depending on format
        """
        context = {}

        # Intent (current direction)
        if include_intent:
            intent_summary = self.intent.summarize(repo_id=self.config.repo_id)
            context["intent"] = {
                "current_focus": intent_summary["focus"]["description"] if intent_summary["focus"] else None,
                "current_task": intent_summary["current_task"]["description"] if intent_summary["current_task"] else None,
                "constraints": intent_summary["constraints"],
                "goals": [g["description"] for g in intent_summary["all_goals"][:5]]
            }

        # Semantic knowledge (what we know)
        if include_knowledge:
            warnings = self.semantic.get_warnings()[:10]
            conventions = self.semantic.get_conventions()[:10]
            known_issues = self.semantic.get_known_issues()[:5]

            context["knowledge"] = {
                "warnings": [w["content"] for w in warnings],
                "conventions": [c["content"] for c in conventions],
                "known_issues": [i["content"] for i in known_issues]
            }

        # Recent history (what happened)
        if include_history:
            recent = self.episodic.recent(limit=10)
            context["history"] = {
                "recent_events": [
                    {
                        "event": e["content"],
                        "category": e["category"],
                        "when": e["created_at"]
                    }
                    for e in recent
                ]
            }

        # Stats
        context["meta"] = {
            "generated_at": datetime.now().isoformat(),
            "stats": self._storage.get_stats()
        }

        if format == "json":
            return context

        # Format as text
        return self._format_context_text(context)

    def _format_context_text(self, context: Dict[str, Any]) -> str:
        """Format context as human/LLM readable text."""
        lines = ["# Project Memory Context", ""]

        # Intent
        if "intent" in context:
            intent = context["intent"]
            lines.append("## Current Direction")

            if intent["current_focus"]:
                lines.append(f"**Focus:** {intent['current_focus']}")

            if intent["current_task"]:
                lines.append(f"**Working on:** {intent['current_task']}")

            if intent["constraints"]:
                lines.append("\n**Constraints:**")
                for c in intent["constraints"]:
                    lines.append(f"- {c}")

            if intent["goals"]:
                lines.append("\n**Active Goals:**")
                for g in intent["goals"]:
                    if not g.startswith("WORKING ON:") and not g.startswith("CONSTRAINT:"):
                        lines.append(f"- {g}")

            lines.append("")

        # Knowledge
        if "knowledge" in context:
            knowledge = context["knowledge"]

            if knowledge["warnings"]:
                lines.append("## Warnings")
                for w in knowledge["warnings"]:
                    lines.append(f"- {w}")
                lines.append("")

            if knowledge["conventions"]:
                lines.append("## Conventions")
                for c in knowledge["conventions"]:
                    lines.append(f"- {c}")
                lines.append("")

            if knowledge["known_issues"]:
                lines.append("## Known Issues")
                for i in knowledge["known_issues"]:
                    lines.append(f"- {i}")
                lines.append("")

        # History
        if "history" in context and context["history"]["recent_events"]:
            lines.append("## Recent Activity")
            for event in context["history"]["recent_events"][:5]:
                lines.append(f"- [{event['category']}] {event['event']}")
            lines.append("")

        return "\n".join(lines)

    # =========================================================================
    # Maintenance
    # =========================================================================

    def compress(self) -> List[str]:
        """
        Run memory compression.

        Compresses old episodic memories into semantic knowledge.

        Returns:
            List of created semantic memory IDs
        """
        return self._compressor.auto_compress()

    def decay(self) -> int:
        """
        Run memory decay.

        Reduces importance of old, unused memories.

        Returns:
            Number of memories affected
        """
        if not self.config.decay_enabled:
            return 0

        return self._compressor.decay_old_memories(
            halflife_days=self.config.decay_halflife_days
        )

    # =========================================================================
    # Quality Management
    # =========================================================================

    def deduplicate(self, layer: str = "episodic", threshold: float = 0.9) -> List[Dict[str, Any]]:
        """Find and list duplicate memories."""
        return self.deduplicator.find_duplicates(layer=layer, threshold=threshold)

    # =========================================================================
    # Maintenance
    # =========================================================================

    def stats(self) -> Dict[str, Any]:
        """Get memory statistics."""
        return self._storage.get_stats(repo_id=self.config.repo_id)

    # =========================================================================
    # Import/Export
    # =========================================================================

    def export(self, path: Path = None) -> Dict[str, Any]:
        """
        Export all memories to JSON.

        Args:
            path: Optional path to save to

        Returns:
            Complete memory export
        """
        # Export filtered by repo_id if set
        repo_id = self.config.repo_id
        
        episodic_memories = self._storage.list_memories(layer="episodic", limit=10000)
        semantic_memories = self._storage.list_memories(layer="semantic", limit=10000)
        
        # Filter if repo_id is set (simple client-side filter since list_memories might be global until updated)
        # Better to update list_memories to accept repo_id, but assuming list_memories will be updated soon:
        # Actually I should pass repo_id to list_memories if I update it.
        # Let's assume I will update list_memories next.
        
        export_data = {
            "version": "1.0",
            "exported_at": datetime.now().isoformat(),
            "config": self.config.model_dump(),
            "memories": {
                "episodic": self._storage.list_memories(layer="episodic", limit=10000, repo_id=repo_id),
                "semantic": self._storage.list_memories(layer="semantic", limit=10000, repo_id=repo_id),
            },
            "intents": self._storage.get_active_intents(repo_id=repo_id),
            "stats": self.stats()
        }

        if path:
            path = Path(path)
            path.write_text(json.dumps(export_data, indent=2, default=str))

        return export_data

    def import_memories(self, path: Path):
        """
        Import memories from JSON export.

        Args:
            path: Path to import file
        """
        data = json.loads(Path(path).read_text())

        # Import episodic memories
        for mem in data.get("memories", {}).get("episodic", []):
            self._storage.store_memory(
                content=mem["content"],
                layer="episodic",
                category=mem.get("category", "note"),
                importance=mem.get("importance", 0.5),
                tags=mem.get("tags", []),
                metadata=mem.get("metadata", {}),
                repo_id=mem.get("repo_id") or self.config.repo_id
            )

        # Import semantic memories
        for mem in data.get("memories", {}).get("semantic", []):
            self._storage.store_memory(
                content=mem["content"],
                layer="semantic",
                category=mem.get("category", "fact"),
                importance=mem.get("importance", 0.5),
                tags=mem.get("tags", []),
                metadata=mem.get("metadata", {}),
                repo_id=mem.get("repo_id") or self.config.repo_id
            )

        # Import intents
        for intent in data.get("intents", []):
            self._storage.set_intent(
                description=intent["description"],
                priority=intent.get("priority", 1),
                context=intent.get("context", {}),
                repo_id=intent.get("repo_id") or self.config.repo_id
            )
