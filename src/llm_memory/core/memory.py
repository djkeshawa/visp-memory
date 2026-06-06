"""
Main Memory System

The unified interface for LLM memory, bringing together:
- Episodic memories (events)
- Semantic memories (knowledge)
- Intent (goals/direction)
- Compression (memory consolidation)
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

from llm_memory.config import MemoryConfig
from llm_memory.core.compression import MemoryCompressor, create_llm_compressor
from llm_memory.core.memory_context import build_context, format_context_text
from llm_memory.core.memory_import_export import export_memory, import_memories
from llm_memory.core.neo4j_storage import Neo4jStorage
from llm_memory.core.ranking import DEFAULT_RECALL_MIN_SCORE, rank_memory_results
from llm_memory.core.remote_storage import RemoteStorage
from llm_memory.core.repository import RepositoryManager
from llm_memory.core.storage import LocalStorage
from llm_memory.core.team import TeamManager
from llm_memory.layers.episodic import EpisodeCategory, EpisodicMemory
from llm_memory.layers.intent import IntentMemory, IntentPriority
from llm_memory.layers.semantic import KnowledgeCategory, SemanticMemory
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

    def __init__(self, config: MemoryConfig = None, data_dir: Path = None):
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

        # Initialize embedding provider for local/server storage. Client-mode MCP delegates
        # storage and embedding work to the configured memory server.
        embedding_fn = None
        if self.config.storage.mode != "client":
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
                api_key=self.config.storage.api_key,
                jwt_token=self.config.storage.jwt_token,
            )
        elif self.config.storage.backend == "neo4j":
            self._storage = Neo4jStorage(
                uri=self.config.storage.neo4j_uri,
                user=self.config.storage.neo4j_user,
                password=self.config.storage.neo4j_password,
                embedding_fn=embedding_fn,
            )
        else:
            self._storage = LocalStorage(self.config.storage.data_dir, embedding_fn=embedding_fn)

        # Initialize layers
        self.episodic = EpisodicMemory(self._storage)
        self.semantic = SemanticMemory(self._storage)
        self.intent = IntentMemory(self._storage)

        # Initialize managers (Phase 3)
        self.repos = RepositoryManager(self._storage)
        self.teams = TeamManager(self._storage)

        # Initialize compressor
        compress_fn = None
        if self.config.compression.llm_provider:
            try:
                compress_fn = create_llm_compressor(
                    provider=self.config.compression.llm_provider,
                    model=self.config.compression.llm_model,
                )
            except Exception:
                pass  # Fall back to heuristic compression

        self._compressor = MemoryCompressor(self._storage, compress_fn)
        self.deduplicator = Deduplicator(self._storage)

        # Initialize conflict detector
        from llm_memory.quality.conflict import ConflictDetector

        # Reuse compressor's LLM logic/keys for now as they are similar
        # Ideally should use dedicated config but this adheres to current structures
        self.conflict_detector = ConflictDetector(
            self._storage,
            provider=self.config.compression.llm_provider,
            model=self.config.compression.llm_model,
            api_key=self.config.embedding.api_key,
        )

    # =========================================================================
    # Quick Access Methods
    # =========================================================================

    def record(self, event: str, category: str = "note", importance: float = 0.5, **kwargs) -> str:
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

        return self.episodic.record(content=event, category=cat, importance=importance, **kwargs)

    def decision(
        self, what: str, why: str, alternatives: List[str] = None, repo_id: str = None
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
            what, why, alternatives, repo_id=repo_id or self.config.repo_id
        )

    def learn(
        self,
        knowledge: str,
        category: str = "fact",
        importance: float = 0.6,
        repo_id: str = None,
        **kwargs,
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

        # Check for conflicts if enabled
        if kwargs.get("detect_conflicts", False) or self.config.quality.conflict_detection:
            conflict = self.check_conflict(knowledge, layer="semantic")
            if conflict:
                # Store conflict info in metadata
                kwargs.setdefault("metadata", {})
                kwargs["metadata"]["conflict"] = conflict
                # Could assume we want to proceed but mark it,
                # or raise error. For now, we proceed and tag.

        return self.semantic.establish(
            knowledge=knowledge,
            category=cat,
            importance=importance,
            repo_id=repo_id or self.config.repo_id,
            **kwargs,
        )

    def check_conflict(self, content: str, layer: str = "semantic") -> Optional[Dict[str, Any]]:
        """
        Check if content conflicts with existing memories.

        Args:
            content: New content to check
            layer: Layer to check against

        Returns:
            Conflict details or None
        """
        # 1. Find relevant memories
        relevant = self._storage.search_memories(
            query=content, layer=layer, limit=5, repo_id=self.config.repo_id
        )

        # 2. Check for conflicts
        return self.conflict_detector.detect_conflicts(content, relevant)

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
        self, goal: str, priority: int = 1, constraints: List[str] = None, repo_id: str = None
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
            repo_id=repo_id or self.config.repo_id,
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
        return self.intent.clear_task(repo_id=self.config.repo_id)

    # =========================================================================
    # Search and Recall
    # =========================================================================

    def recall(
        self,
        query: str,
        layers: List[str] = None,
        repo_id: str = None,
        limit: int = 10,
        min_score: float = DEFAULT_RECALL_MIN_SCORE,
        status: str = "active",
    ) -> List[Dict[str, Any]]:
        """
        Search across all memory layers.

        Args:
            query: Search query (natural language)
            layers: Which layers to search (default: all)
            limit: Maximum results per layer
            min_score: Minimum canonical relevance score to return

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
                layer=layer,
                repo_id=search_repo_id,
                limit=limit,
                status=status,
            )
            results.extend(layer_results)

        return rank_memory_results(results, query=query, limit=limit, min_score=min_score)

    def graph_neighbors(
        self,
        memory_id: str,
        relationship_filter: str = None,
        repo_id: str = None,
        depth: int = 1,
        token_budget: int = 2000,
        limit: int = 25,
    ) -> Dict[str, Any]:
        """Return a compact relationship neighborhood for a memory."""
        from llm_memory.recall.graph import GraphRecall

        return GraphRecall(self._storage).neighbors(
            memory_id=memory_id,
            relationship_filter=relationship_filter,
            repo_id=repo_id or self.config.repo_id,
            depth=depth,
            token_budget=token_budget,
            limit=limit,
        )

    def graph_path(
        self,
        source_id: str,
        target_id: str,
        repo_id: str = None,
        max_hops: int = 4,
        token_budget: int = 2000,
    ) -> Dict[str, Any]:
        """Return the shortest evidence-backed relationship path between two memories."""
        from llm_memory.recall.graph import GraphRecall

        return GraphRecall(self._storage).path(
            source_id=source_id,
            target_id=target_id,
            repo_id=repo_id or self.config.repo_id,
            max_hops=max_hops,
            token_budget=token_budget,
        )

    def graph_trace(
        self,
        query: str,
        repo_id: str = None,
        depth: int = 2,
        token_budget: int = 2000,
        limit: int = 5,
        relationship_filter: str = None,
    ) -> Dict[str, Any]:
        """Return ranked seed memories plus evidence-backed relationship context."""
        from llm_memory.recall.graph import GraphRecall

        return GraphRecall(self._storage).trace(
            query=query,
            repo_id=repo_id or self.config.repo_id,
            depth=depth,
            token_budget=token_budget,
            limit=limit,
            relationship_filter=relationship_filter,
        )

    def graph_why_relevant(
        self,
        query: str,
        memory_id: str,
        repo_id: str = None,
        depth: int = 2,
        token_budget: int = 2000,
        limit: int = 5,
    ) -> Dict[str, Any]:
        """Explain why a specific memory is relevant to a query."""
        from llm_memory.recall.graph import GraphRecall

        return GraphRecall(self._storage).why_relevant(
            query=query,
            memory_id=memory_id,
            repo_id=repo_id or self.config.repo_id,
            depth=depth,
            token_budget=token_budget,
            limit=limit,
        )

    def relevant_for(
        self, task: str = None, files: List[str] = None, limit: int = 15
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
        results = {"knowledge": [], "warnings": [], "history": []}

        # Get relevant semantic knowledge
        repo_id = self.config.repo_id
        if task or files:
            results["knowledge"] = self.semantic.relevant_for(
                files=files, query=task, limit=limit, repo_id=repo_id
            )

        # Get warnings for files
        if files:
            for f in files:
                warnings = self.semantic.get_warnings(f, repo_id=repo_id)
                results["warnings"].extend(warnings)

        # Get relevant history
        if task:
            results["history"] = self.episodic.search(task, limit=limit // 2, repo_id=repo_id)

        return results

    # =========================================================================
    # Context Generation
    # =========================================================================

    def context(
        self,
        include_history: bool = True,
        include_knowledge: bool = True,
        include_intent: bool = True,
        format: str = "text",
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
        context = build_context(
            self,
            include_history=include_history,
            include_knowledge=include_knowledge,
            include_intent=include_intent,
        )

        if format == "json":
            return context

        # Format as text
        return self._format_context_text(context)

    def _format_context_text(self, context: Dict[str, Any]) -> str:
        """Format context as human/LLM readable text."""
        return format_context_text(context)

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

        return self._compressor.decay_old_memories(halflife_days=self.config.decay_halflife_days)

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
        return export_memory(self, path)

    def import_memories(self, path: Path):
        """
        Import memories from JSON export.

        Args:
            path: Path to import file
        """
        import_memories(self, path)
