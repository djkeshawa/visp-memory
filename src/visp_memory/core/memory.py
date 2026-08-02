"""
Main Memory System

The unified interface for LLM memory, bringing together:
- Episodic memories (events)
- Semantic memories (knowledge)
- Intent (goals/direction)
- Compression (memory consolidation)
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from visp_memory.config import MemoryConfig
from visp_memory.core.arcadedb_storage import ArcadeDbStorage
from visp_memory.core.compression import MemoryCompressor, create_llm_compressor
from visp_memory.core.eligibility import (
    EligibilityFilterResult,
    filter_recall_eligible,
    require_repo_id,
)
from visp_memory.core.memory_context import build_context, format_context_text
from visp_memory.core.memory_import_export import export_memory, import_memories
from visp_memory.core.neo4j_storage import Neo4jStorage
from visp_memory.core.ranking import DEFAULT_RECALL_MIN_SCORE, rank_memory_results, text_similarity
from visp_memory.core.remote_storage import RemoteStorage
from visp_memory.core.repository import RepositoryManager
from visp_memory.core.storage import UNSCOPED_REPO_ID, LocalStorage
from visp_memory.core.team import TeamManager
from visp_memory.core.trust import (
    TrustFilterResult,
    WriteChannel,
    channel_policy,
    filter_unsolicited,
)
from visp_memory.layers.episodic import EpisodeCategory, EpisodicMemory
from visp_memory.layers.intent import IntentMemory, IntentPriority
from visp_memory.layers.semantic import KnowledgeCategory, SemanticMemory
from visp_memory.quality.dedup import Deduplicator

logger = logging.getLogger(__name__)


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
        #
        # An explicit "none" provider disables embeddings entirely (embedding_fn stays
        # None) so backends use keyword/text search. This is distinct from "noop", which
        # yields a constant vector — fine for ChromaDB-managed embeddings, but on backends
        # that run their own vector index (e.g. Neo4j) a constant vector makes every result
        # score identically, so "none" is the correct way to turn vector search off.
        embedding_fn = None
        if self.config.storage.mode != "client" and self.config.embedding.provider != "none":
            try:
                from visp_memory.core.embeddings import get_embedding_provider

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
        elif self.config.storage.backend == "arcadedb":
            self._storage = ArcadeDbStorage(
                self.config.storage.data_dir,
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

        from visp_memory.quality.reconcile import Reconciler

        self.reconciler = Reconciler(
            self._storage,
            noop_threshold=self.config.quality.reconcile_noop_threshold,
            update_threshold=self.config.quality.reconcile_update_threshold,
        )

        # Initialize conflict detector
        from visp_memory.quality.conflict import ConflictDetector

        # Reuse compressor's LLM logic/keys for now as they are similar
        # Ideally should use dedicated config but this adheres to current structures
        self.conflict_detector = ConflictDetector(
            self._storage,
            provider=self.config.compression.llm_provider,
            model=self.config.compression.llm_model,
            api_key=self.config.embedding.api_key,
        )

    def close(self) -> None:
        """Release storage resources (e.g. the Neo4j driver connection pool).

        Idempotent: safe to call more than once, since the underlying backend
        ``close()`` implementations are idempotent.
        """
        storage = getattr(self, "_storage", None)
        if storage is not None:
            storage.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

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
        write_channel = kwargs.pop("_write_channel", WriteChannel.LIBRARY)

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
            _write_channel=write_channel,
            **kwargs,
        )

    def decision(
        self,
        what: str,
        why: str,
        alternatives: List[str] = None,
        repo_id: str = None,
        tags: List[str] = None,
        *,
        _write_channel: WriteChannel = WriteChannel.LIBRARY,
    ) -> str:
        """
        Quick method to record a decision.

        Args:
            what: What was decided
            why: Why this choice was made
            alternatives: What alternatives were considered
            repo_id: Optional repository context (defaults to config.repo_id)
            tags: Organizational tags. Caller-supplied provenance labels are replaced
                by the package-owned write-channel policy.

        Returns:
            Memory ID
        """
        return self.episodic.decision(
            what,
            why,
            alternatives,
            repo_id=repo_id or self.config.repo_id,
            tags=tags,
            _write_channel=_write_channel,
        )

    def learn(
        self,
        knowledge: str,
        category: str = "fact",
        importance: float = 0.6,
        repo_id: str = None,
        *,
        _write_channel: WriteChannel = WriteChannel.LIBRARY,
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

        # `detect_conflicts`/`reconcile` are control flags for this method only;
        # they must not be forwarded to establish() (which does not accept them).
        detect = kwargs.pop("detect_conflicts", False)
        reconcile = kwargs.pop("reconcile", None)
        if reconcile is None:
            reconcile = self.config.quality.write_reconciliation

        effective_repo_id = repo_id or self.config.repo_id or UNSCOPED_REPO_ID
        category_value = cat.value if hasattr(cat, "value") else str(cat)
        evidence_ids = list(kwargs.pop("evidence_ids", []) or [])
        source_episodes = list(kwargs.get("source_episodes", []) or [])
        if not evidence_ids and not source_episodes:
            policy = channel_policy(_write_channel)
            evidence_repo_id = effective_repo_id
            if evidence_repo_id != UNSCOPED_REPO_ID:
                evidence_repo_id = require_repo_id(evidence_repo_id)
            evidence_ids = [
                self._storage.store_evidence(
                    knowledge,
                    repo_id=evidence_repo_id,
                    evidence_type="caller_input",
                    provenance=policy.provenance.value,
                    metadata={"write_channel": _write_channel.value},
                )
            ]

        # Write-time reconciliation: fold near-duplicate knowledge into the
        # existing memory instead of inserting a copy. Keeps the store small,
        # cheap to retrieve, and self-correcting (Mem0's ADD/UPDATE/NOOP model).
        if reconcile:
            decision = self.reconciler.decide(
                knowledge, layer="semantic", repo_id=effective_repo_id, category=category_value
            )
            if decision.action in ("noop", "update") and decision.target_id:
                return self._apply_reconcile_decision(
                    decision,
                    knowledge,
                    importance,
                    evidence_ids=evidence_ids,
                    repo_id=effective_repo_id,
                )

        conflict = None
        if detect or self.config.quality.conflict_detection:
            conflict = self.check_conflict(knowledge, layer="semantic")

        memory_id = self.semantic.establish(
            knowledge=knowledge,
            category=cat,
            importance=importance,
            repo_id=effective_repo_id,
            evidence_ids=evidence_ids,
            _write_channel=_write_channel,
            **kwargs,
        )

        # Record contradictions as explicit graph relationships rather than as
        # opaque metadata: this keeps conflicting knowledge discoverable through
        # the relationship graph instead of silently co-existing, and avoids
        # passing an unsupported `metadata=` kwarg into establish().
        if conflict and conflict.get("conflicting_ids"):
            reason = conflict.get("reason", "Detected contradictory knowledge")
            for conflicting_id in conflict["conflicting_ids"]:
                try:
                    self._storage.add_relationship(
                        memory_id,
                        conflicting_id,
                        "contradicts",
                        evidence={"reason": reason, "source": "conflict_detection"},
                    )
                except Exception as exc:
                    logger.warning(
                        "Failed to link contradiction %s -> %s: %s",
                        memory_id,
                        conflicting_id,
                        exc,
                    )
                if self.config.quality.auto_supersede:
                    self._supersede_memory(conflicting_id, superseded_by=memory_id, reason=reason)

        return memory_id

    def _apply_reconcile_decision(
        self,
        decision,
        knowledge: str,
        importance: float,
        *,
        evidence_ids: List[str],
        repo_id: str,
    ) -> str:
        """Reinforce or refresh an existing memory instead of inserting a duplicate."""
        from visp_memory.core.clock import utc_now_iso

        existing = self._storage.get_memory(decision.target_id)
        if not existing:  # pragma: no cover - race between decide and apply
            return decision.target_id
        merged_importance = max(float(existing.get("importance", 0.5) or 0.0), float(importance))
        updates: Dict[str, Any] = {"importance": merged_importance}
        if decision.action == "update":
            updates["content"] = knowledge
            updates["metadata"] = {
                **(existing.get("metadata") or {}),
                "reconciled_at": utc_now_iso(),
                "reconcile_action": "update",
            }
        if evidence_ids:
            evidence_repo_id = repo_id
            if evidence_repo_id != UNSCOPED_REPO_ID:
                evidence_repo_id = require_repo_id(evidence_repo_id)
            self._storage.attach_evidence(
                decision.target_id,
                evidence_ids,
                repo_id=evidence_repo_id,
            )
        self._storage.update_memory(decision.target_id, **updates)
        logger.info(
            "Reconciled knowledge into %s (%s, overlap=%.2f): %s",
            decision.target_id,
            decision.action,
            decision.similarity,
            decision.reason,
        )
        return decision.target_id

    def _supersede_memory(self, memory_id: str, superseded_by: str, reason: str) -> None:
        """Non-destructively invalidate a contradicted memory (belief revision).

        The memory keeps its content and relationships but leaves the active set
        (status="superseded"), so recall prioritizes current knowledge while the
        old belief stays auditable and restorable. ``invalid_at`` records when the
        belief stopped being held (bi-temporal validity, Zep-style).
        """
        from visp_memory.core.clock import utc_now_iso

        try:
            existing = self._storage.get_memory(memory_id)
            if not existing:
                return
            self._storage.update_memory(
                memory_id,
                status="superseded",
                metadata={
                    **(existing.get("metadata") or {}),
                    "superseded_by": superseded_by,
                    "invalid_at": utc_now_iso(),
                    "superseded_reason": reason,
                },
            )
        except Exception as exc:  # supersession must never block the new write
            logger.warning("Failed to supersede memory %s: %s", memory_id, exc)

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

    def warn(
        self,
        area: str,
        warning: str,
        severity: float = 0.7,
        repo_id: str = None,
        tags: List[str] = None,
        *,
        _write_channel: WriteChannel = WriteChannel.LIBRARY,
    ) -> str:
        """
        Quick method to establish a warning.

        Args:
            area: What area
            warning: What to watch out for
            severity: How serious
            repo_id: Optional repository context
            tags: Organizational tags. Caller-supplied provenance labels are replaced
                by the package-owned write-channel policy.

        Returns:
            Memory ID
        """
        return self.semantic.warn(
            area,
            warning,
            severity,
            repo_id=repo_id or self.config.repo_id,
            tags=tags,
            _write_channel=_write_channel,
        )

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

    def done(
        self,
        *,
        actor_id: str = "library-caller",
        channel: WriteChannel | str = WriteChannel.LIBRARY,
    ) -> int:
        """Record task completion outcomes without changing intent status."""
        return self.intent.clear_task(
            repo_id=self.config.repo_id,
            actor_id=actor_id,
            channel=channel,
        )

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
        log_utility: bool = False,
        task_id: str = None,
        task: str = None,
        files: List[str] = None,
        session_id: str = None,
        constraints: List[str] = None,
        dependencies: List[str] = None,
        environment: Any = None,
        task_type: Any = None,
        as_of: Any = None,
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

        search_repo_id = require_repo_id(repo_id or self.config.repo_id)

        for layer in layers:
            layer_results = self._storage.search_memories(
                query=query,
                layer=layer,
                repo_id=search_repo_id,
                limit=limit,
                status=status,
                environment=environment,
                task_type=task_type,
                as_of=as_of,
            )
            results.extend(layer_results)

        eligibility = filter_recall_eligible(
            results,
            repo_id=search_repo_id,
            environment=environment,
            task_type=task_type,
            as_of=as_of,
        )
        self.last_recall_eligibility_result = eligibility
        self.last_recall_eligibility = eligibility.diagnostics()

        ranked = self.rank_with_context(
            eligibility.allowed,
            query=query,
            repo_id=search_repo_id,
            task=task,
            files=files,
            session_id=session_id,
            constraints=constraints,
            dependencies=dependencies,
            limit=limit,
            min_score=min_score,
        )
        if log_utility:
            for result in ranked:
                memory_id = result.get("id")
                if memory_id:
                    self.record_utility_feedback(
                        memory_id=memory_id,
                        event_type="surfaced",
                        repo_id=search_repo_id,
                        query=query,
                        task_id=task_id,
                        metadata={"source": "recall"},
                    )
        return ranked

    def rank_with_context(
        self,
        memories: List[Dict[str, Any]],
        query: str = None,
        repo_id: str = None,
        task: str = None,
        files: List[str] = None,
        session_id: str = None,
        constraints: List[str] = None,
        dependencies: List[str] = None,
        limit: int = None,
        min_score: float = None,
        include_active_intents: bool = True,
    ) -> List[Dict[str, Any]]:
        """Rank memory rows with intent-aware contextual factors attached."""
        context = self._build_recall_factor_context(
            repo_id=repo_id,
            task=task,
            files=files,
            session_id=session_id,
            constraints=constraints,
            dependencies=dependencies,
            include_active_intents=include_active_intents,
        )
        annotated = []
        for memory in memories:
            item = dict(memory)
            factors = self._recall_ranking_factors(item, context)
            if factors:
                item["ranking_factors"] = factors
            annotated.append(item)
        return rank_memory_results(annotated, query=query, limit=limit, min_score=min_score)

    def _build_recall_factor_context(
        self,
        repo_id: str = None,
        task: str = None,
        files: List[str] = None,
        session_id: str = None,
        constraints: List[str] = None,
        dependencies: List[str] = None,
        include_active_intents: bool = True,
    ) -> Dict[str, Any]:
        active_intents = self.intent.get_active(repo_id=repo_id) if include_active_intents else []
        current_task = (
            self.intent.get_working_on(repo_id=repo_id) if include_active_intents else None
        )
        task_text = task or self._intent_task_text(current_task)
        file_values = self._context_values(files)
        if not file_values and current_task:
            current_context = current_task.get("context") or {}
            if isinstance(current_context, dict):
                file_values = self._context_values(current_context.get("files"))

        constraint_values = self._context_values(constraints)
        if include_active_intents:
            constraint_values.extend(self._context_values(self.intent.get_constraints(repo_id=repo_id)))

        dependency_values = self._context_values(dependencies)
        if repo_id:
            try:
                dependency_values.extend(
                    dep.target_repo_id for dep in self.repos.get_dependencies(repo_id)
                )
            except (NotImplementedError, ValueError):
                pass

        return {
            "repo_id": repo_id,
            "task": task_text,
            "files": self._unique_values(file_values),
            "session_id": session_id,
            "constraints": self._unique_values(constraint_values),
            "dependencies": self._unique_values(dependency_values),
            "active_intents": active_intents,
        }

    def _recall_ranking_factors(
        self, memory: Dict[str, Any], context: Dict[str, Any]
    ) -> Dict[str, Dict[str, Any]]:
        text = self._recall_factor_text(memory)
        factors: Dict[str, Dict[str, Any]] = {}

        session_id = context.get("session_id")
        if session_id and self._context_value_matches(text, session_id):
            factors["session"] = {"score": 1.0, "reason": f"matched session {session_id}"}

        task = context.get("task")
        if task:
            score = text_similarity(task, text)
            if score > 0:
                factors["task"] = {"score": score, "reason": "matched current task"}

        file_score, file_reason = self._file_factor(text, context.get("files") or [])
        if file_score > 0:
            factors["file"] = {"score": file_score, "reason": file_reason}

        repo_id = context.get("repo_id")
        if repo_id and memory.get("repo_id") == repo_id:
            factors["repo"] = {"score": 1.0, "reason": f"matched repo {repo_id}"}

        dep_score, dep_reason = self._dependency_factor(
            memory, text, context.get("dependencies") or []
        )
        if dep_score > 0:
            factors["dependency"] = {"score": dep_score, "reason": dep_reason}

        constraint_score = self._best_text_factor(context.get("constraints") or [], text)
        if constraint_score > 0:
            factors["constraint"] = {
                "score": constraint_score,
                "reason": "matched active constraint",
            }

        intent_score = self._active_intent_factor(context.get("active_intents") or [], text)
        if intent_score > 0:
            factors["active_intent"] = {
                "score": intent_score,
                "reason": "matched active intent",
            }

        return factors

    @staticmethod
    def _context_values(values: Any) -> List[str]:
        if values is None:
            return []
        if isinstance(values, str):
            return [values] if values else []
        if isinstance(values, (list, tuple, set)):
            return [str(value) for value in values if value]
        return [str(values)]

    @staticmethod
    def _unique_values(values: List[str]) -> List[str]:
        return list(dict.fromkeys(value for value in values if value))

    @staticmethod
    def _intent_task_text(intent: Dict[str, Any] = None) -> str | None:
        if not intent:
            return None
        description = str(intent.get("description") or "")
        return description.replace("WORKING ON: ", "", 1) if description else None

    @staticmethod
    def _recall_factor_text(memory: Dict[str, Any]) -> str:
        parts = [
            memory.get("content"),
            memory.get("category"),
            memory.get("repo_id"),
            memory.get("layer"),
        ]
        for field in ("metadata", "tags", "source_ids"):
            value = memory.get(field)
            if value:
                parts.append(json.dumps(value, sort_keys=True, default=str))
        return " ".join(str(part) for part in parts if part).lower().replace("\\", "/")

    @staticmethod
    def _context_value_matches(text: str, value: str) -> bool:
        return str(value).lower().replace("\\", "/") in text

    @classmethod
    def _file_factor(cls, text: str, files: List[str]) -> tuple[float, str | None]:
        for file_path in files:
            normalized = str(file_path).lower().replace("\\", "/")
            if normalized and normalized in text:
                return 1.0, f"matched file {file_path}"
            basename = Path(normalized).name
            if basename and basename in text:
                return 0.6, f"matched file name {basename}"
        return 0.0, None

    @classmethod
    def _dependency_factor(
        cls, memory: Dict[str, Any], text: str, dependencies: List[str]
    ) -> tuple[float, str | None]:
        memory_repo = memory.get("repo_id")
        for dependency in dependencies:
            dep = str(dependency)
            normalized = dep.lower()
            if memory_repo == dep:
                return 1.0, f"matched dependency repo {dep}"
            if normalized and normalized in text:
                return 0.7, f"mentioned dependency {dep}"
        return 0.0, None

    @staticmethod
    def _best_text_factor(values: List[str], text: str) -> float:
        return max((text_similarity(value, text) for value in values), default=0.0)

    @classmethod
    def _active_intent_factor(cls, intents: List[Dict[str, Any]], text: str) -> float:
        values = []
        for intent in intents:
            values.append(str(intent.get("description") or ""))
            context = intent.get("context") or {}
            if isinstance(context, dict):
                values.extend(cls._context_values(context.get("constraints")))
                values.extend(cls._context_values(context.get("files")))
        return cls._best_text_factor(values, text)

    def record_utility_feedback(
        self,
        memory_id: str,
        event_type: str,
        repo_id: str = None,
        query: str = None,
        task_id: str = None,
        outcome: str = None,
        metadata: Dict[str, Any] = None,
    ) -> str:
        """Record a privacy-conscious utility feedback event for a memory."""
        log_event = getattr(self._storage, "log_recall_event", None)
        if not callable(log_event):
            backend = type(self._storage).__name__
            raise NotImplementedError(
                f"Recall utility feedback is not supported by the {backend} backend; "
                "use local SQLite storage for this feature."
            )
        return log_event(
            memory_id=memory_id,
            event_type=event_type,
            repo_id=repo_id,
            query=query,
            task_id=task_id,
            outcome=outcome,
            metadata=metadata,
        )

    def inspect_utility_signals(
        self,
        memory_id: str = None,
        repo_id: str = None,
        event_type: str = None,
        limit: int = 50,
    ) -> Dict[str, Any]:
        """Inspect aggregate recall utility signals."""
        inspector = getattr(self._storage, "inspect_recall_utility", None)
        if not callable(inspector):
            backend = type(self._storage).__name__
            raise NotImplementedError(
                f"Recall utility inspection is not supported by the {backend} backend; "
                "use local SQLite storage for this feature."
            )
        return inspector(
            memory_id=memory_id,
            repo_id=repo_id,
            event_type=event_type,
            limit=limit,
        )

    def reset_utility_signals(
        self,
        memory_id: str = None,
        repo_id: str = None,
        event_type: str = None,
    ) -> int:
        """Reset recall utility signals matching optional filters."""
        resetter = getattr(self._storage, "reset_recall_utility", None)
        if not callable(resetter):
            backend = type(self._storage).__name__
            raise NotImplementedError(
                f"Recall utility reset is not supported by the {backend} backend; "
                "use local SQLite storage for this feature."
            )
        return resetter(memory_id=memory_id, repo_id=repo_id, event_type=event_type)

    def graph_neighbors(
        self,
        memory_id: str,
        relationship_filter: str = None,
        repo_id: str = None,
        depth: int = 1,
        token_budget: int = 2000,
        limit: int = 25,
        environment: Any = None,
        task_type: Any = None,
        as_of: Any = None,
    ) -> Dict[str, Any]:
        """Return a compact relationship neighborhood for a memory."""
        from visp_memory.recall.graph import GraphRecall

        return GraphRecall(self._storage).neighbors(
            memory_id=memory_id,
            relationship_filter=relationship_filter,
            repo_id=repo_id or self.config.repo_id,
            depth=depth,
            token_budget=token_budget,
            limit=limit,
            environment=environment,
            task_type=task_type,
            as_of=as_of,
        )

    def graph_path(
        self,
        source_id: str,
        target_id: str,
        repo_id: str = None,
        max_hops: int = 4,
        token_budget: int = 2000,
        environment: Any = None,
        task_type: Any = None,
        as_of: Any = None,
    ) -> Dict[str, Any]:
        """Return the shortest evidence-backed relationship path between two memories."""
        from visp_memory.recall.graph import GraphRecall

        return GraphRecall(self._storage).path(
            source_id=source_id,
            target_id=target_id,
            repo_id=repo_id or self.config.repo_id,
            max_hops=max_hops,
            token_budget=token_budget,
            environment=environment,
            task_type=task_type,
            as_of=as_of,
        )

    def graph_trace(
        self,
        query: str,
        repo_id: str = None,
        depth: int = 2,
        token_budget: int = 2000,
        limit: int = 5,
        relationship_filter: str = None,
        environment: Any = None,
        task_type: Any = None,
        as_of: Any = None,
    ) -> Dict[str, Any]:
        """Return ranked seed memories plus evidence-backed relationship context."""
        from visp_memory.recall.graph import GraphRecall

        return GraphRecall(self._storage).trace(
            query=query,
            repo_id=repo_id or self.config.repo_id,
            depth=depth,
            token_budget=token_budget,
            limit=limit,
            relationship_filter=relationship_filter,
            environment=environment,
            task_type=task_type,
            as_of=as_of,
        )

    def graph_why_relevant(
        self,
        query: str,
        memory_id: str,
        repo_id: str = None,
        depth: int = 2,
        token_budget: int = 2000,
        limit: int = 5,
        environment: Any = None,
        task_type: Any = None,
        as_of: Any = None,
    ) -> Dict[str, Any]:
        """Explain why a specific memory is relevant to a query."""
        from visp_memory.recall.graph import GraphRecall

        return GraphRecall(self._storage).why_relevant(
            query=query,
            memory_id=memory_id,
            repo_id=repo_id or self.config.repo_id,
            depth=depth,
            token_budget=token_budget,
            limit=limit,
            environment=environment,
            task_type=task_type,
            as_of=as_of,
        )

    def relevant_for(
        self,
        task: str = None,
        files: List[str] = None,
        limit: int = 15,
        repo_id: str = None,
        environment: Any = None,
        task_type: Any = None,
        as_of: Any = None,
    ) -> Dict[str, Any]:
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
        repo_id = require_repo_id(repo_id or self.config.repo_id)
        if task or files:
            results["knowledge"] = self.semantic.relevant_for(
                files=files,
                query=task,
                limit=limit,
                repo_id=repo_id,
                environment=environment,
                task_type=task_type,
                as_of=as_of,
            )

        # Get warnings for files
        if files:
            for f in files:
                warnings = self.semantic.get_warnings(f, repo_id=repo_id)
                results["warnings"].extend(warnings)

        # Get relevant history
        if task:
            results["history"] = self.episodic.search(
                task,
                limit=limit // 2,
                repo_id=repo_id,
                environment=environment,
                task_type=task_type,
                as_of=as_of,
            )

        trust_results = []
        eligibility_results = []
        for group in ("knowledge", "warnings", "history"):
            eligibility = filter_recall_eligible(
                results[group],
                repo_id=repo_id,
                environment=environment,
                task_type=task_type,
                as_of=as_of,
            )
            eligibility_results.append(eligibility)
            filtered = filter_unsolicited(eligibility.allowed)
            results[group] = filtered.allowed
            trust_results.append(filtered)
        results["trust_filter"] = TrustFilterResult.combine(trust_results).diagnostics()
        results["eligibility_filter"] = EligibilityFilterResult.combine(
            eligibility_results
        ).diagnostics()

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
        repo_id: str = None,
        environment: Any = None,
        task_type: Any = None,
        as_of: Any = None,
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
            repo_id: Repository scope (defaults to the configured repo_id)

        Returns:
            Context string or dict depending on format
        """
        context = build_context(
            self,
            include_history=include_history,
            include_knowledge=include_knowledge,
            include_intent=include_intent,
            repo_id=repo_id,
            environment=environment,
            task_type=task_type,
            as_of=as_of,
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

    def token_efficiency(self, repo_id: str = None) -> Dict[str, Any]:
        """Quantify how the memory layer reduces tokens.

        The core promise of Visp Memory is that a small, pre-formed context lets an
        assistant skip re-reading files and re-deriving knowledge every session.
        This method reports two distinct signals and is careful not to conflate them:

        - ``consolidation``: source-backed savings from compressing many episodic
          memories into compact semantic knowledge. Both the originals and the result
          are held, so the saving is directly derived from compression lineage (token
          counts are estimates, but the delta is not hypothetical). This is the only
          figure counted as "saved".
        - ``context``: a descriptive *compactness ratio* of the injected project context
          versus the full active store. This is informational (an assistant would not
          dump the whole store), so it is reported but NOT added to ``saved_tokens``.

        Returns a structured, JSON-serializable summary. ``saved_tokens`` is
        conservative (never negative) so the system does not overclaim.
        """
        from visp_memory.core.tokens import TokenSavings, estimate_total_tokens

        def _as_int(value: Any) -> int:
            try:
                return max(0, int(value or 0))
            except (TypeError, ValueError):
                return 0

        repo_id = repo_id or self.config.repo_id

        semantic = self._storage.list_memories(
            layer="semantic", repo_id=repo_id, status="active", limit=10000
        )
        source_tokens = 0
        result_tokens = 0
        consolidations = 0
        for memory in semantic:
            savings = (memory.get("metadata") or {}).get("token_savings")
            if not isinstance(savings, dict):
                continue
            source_tokens += _as_int(savings.get("source_tokens"))
            result_tokens += _as_int(savings.get("result_tokens"))
            consolidations += 1
        consolidation = TokenSavings(source_tokens, result_tokens).as_dict()
        consolidation["consolidations"] = consolidations

        episodic = self._storage.list_memories(
            layer="episodic", repo_id=repo_id, status="active", limit=10000
        )
        context_tokens = estimate_total_tokens([self.context(format="text", repo_id=repo_id)])
        full_store_tokens = estimate_total_tokens(
            [m.get("content", "") for m in episodic] + [m.get("content", "") for m in semantic]
        )
        compactness_ratio = (
            round(1.0 - (context_tokens / full_store_tokens), 4) if full_store_tokens > 0 else 0.0
        )
        context = {
            "context_tokens": context_tokens,
            "full_store_tokens": full_store_tokens,
            "compactness_ratio": max(0.0, compactness_ratio),
        }

        return {
            "repo_id": repo_id,
            "consolidation": consolidation,
            "context": context,
            # Only auditable consolidation savings count as "saved"; context compactness
            # is descriptive and intentionally excluded to avoid overclaiming.
            "total_saved_tokens": consolidation["saved_tokens"],
        }

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
