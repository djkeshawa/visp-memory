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
from typing import Any, Dict, List

from visp_memory.config import MemoryConfig
from visp_memory.core.arcadedb_storage import ArcadeDbStorage
from visp_memory.core.compression import MemoryCompressor, create_llm_compressor
from visp_memory.core.eligibility import (
    EligibilityFilterResult,
    filter_recall_eligible,
    require_repo_id,
)
from visp_memory.core.embedding_status import (
    ENABLE_SEMANTIC_RECALL_REMEDIATION,
    ENABLE_VECTOR_INDEX_REMEDIATION,
    PROVIDER_INIT_FAILED,
    is_noop_provider,
    produces_no_vectors,
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
from visp_memory.quality.conflict import ConflictVerdict
from visp_memory.quality.dedup import Deduplicator, DedupReport
from visp_memory.quality.secrets import redact_for_storage

logger = logging.getLogger(__name__)


class ContradictionNotRecordedError(RuntimeError):
    """A contradiction was detected but its edge could not be written.

    Raised rather than logged because the edge is the only thing that makes the
    disagreement discoverable. Without it the two beliefs co-exist and whichever
    the search surfaces first is treated as fact — so a silent failure here is
    indistinguishable from never having detected the conflict at all.
    """


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
        # The provider that was actually built, which is not the one that was
        # configured whenever auto-selection falls through. Recorded so callers can
        # label a score for what it is instead of assuming vectors produced it.
        # None means this process cannot tell (client mode: the server chooses).
        self._embedding_provider_name: str = None
        if self.config.storage.mode != "client":
            if self.config.embedding.provider == "none":
                self._embedding_provider_name = "none"
            else:
                try:
                    from visp_memory.core.embeddings import get_embedding_provider

                    embedder = get_embedding_provider(self.config.embedding)
                    self._embedding_provider_name = getattr(embedder, "provider_name", None)
                    embedding_fn = embedder.embed
                except Exception as e:
                    # Embedding provider initialization failed - will use fallback search
                    import logging

                    logging.warning(f"Failed to initialize embedding provider: {e}")
                    # No embedding function reaches storage, so recall runs on the
                    # text path exactly as it does under noop. Reported under its own
                    # name rather than as "none": a host reading this field must be
                    # able to tell an operator's choice from a fault it should raise.
                    self._embedding_provider_name = PROVIDER_INIT_FAILED

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

        # The verdict of the last projection read, so a null structural signal can
        # always say why. None until something asks for the graph.
        self.last_code_graph_load: Dict[str, Any] = None

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
        cat = KnowledgeCategory(category)

        # `detect_conflicts`/`reconcile` are control flags for this method only;
        # they must not be forwarded to establish() (which does not accept them).
        detect = kwargs.pop("detect_conflicts", False)
        reconcile = kwargs.pop("reconcile", None)
        if "epistemic_status" in kwargs:
            raise ValueError(
                "initial epistemic status is assigned by the memory service"
            )
        if reconcile is None:
            reconcile = self.config.quality.write_reconciliation

        # Sanitize before creating Evidence or invoking conflict/reconciliation. Storage
        # repeats this check as defense in depth, but the facade is the first boundary
        # that can keep raw input out of every downstream model/search call.
        knowledge, quality_flags = redact_for_storage(
            knowledge,
            kwargs.get("quality_flags"),
            reject_if_redacted=kwargs.get("authority_attestation") is not None,
        )
        if quality_flags is not None:
            kwargs["quality_flags"] = quality_flags

        effective_repo_id = repo_id or self.config.repo_id or UNSCOPED_REPO_ID
        category_value = cat.value if hasattr(cat, "value") else str(cat)
        if category_value == KnowledgeCategory.PROHIBITION.value:
            reconcile = False
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
        # Conflict detection runs BEFORE reconciliation. Reconciling folds new
        # knowledge into an existing memory, and folding away a contradiction
        # hides it: the reconcile branch used to return before this check ever
        # ran, so a near-duplicate that disagreed was merged silently, with no
        # `contradicts` edge and nothing recorded (MG-025).
        verdict = ConflictVerdict.clear()
        if detect or self.config.quality.conflict_detection:
            verdict = self.check_conflict(
                knowledge, layer="semantic", repo_id=effective_repo_id
            )

        # An undetermined verdict is not a clear one. The content is still stored
        # — refusing would break every deployment without a detector configured,
        # which is most of them — but it is marked so nothing downstream can
        # mistake "not checked" for "checked and clean" (MG-026).
        if not verdict.determined:
            kwargs["quality_flags"] = list(
                dict.fromkeys(
                    [*(kwargs.get("quality_flags") or []), "conflict_unverified"]
                )
            )

        # Only reconcile once we know there is nothing to contradict. A confirmed
        # conflict must be recorded as its own memory and edge, never merged.
        if reconcile and not verdict.has_conflict:
            decision = self.reconciler.decide(
                knowledge, layer="semantic", repo_id=effective_repo_id, category=category_value
            )
            if decision.action in ("noop", "update") and decision.target_id:
                return self._apply_reconcile_decision(
                    decision,
                    knowledge,
                    importance,
                    evidence_ids=evidence_ids,
                    source_episodes=source_episodes,
                    repo_id=effective_repo_id,
                    write_channel=_write_channel,
                )

        conflict = verdict.conflict

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
            reason, _ = redact_for_storage(str(reason), None)
            for conflicting_id in conflict["conflicting_ids"]:
                try:
                    self._storage.add_relationship(
                        memory_id,
                        conflicting_id,
                        "contradicts",
                        evidence={"reason": reason, "source": "conflict_detection"},
                    )
                except Exception as exc:
                    # A contradiction that could not be recorded must not leave an
                    # active memory behind claiming success. The edge is the only
                    # thing making the conflict discoverable; without it the two
                    # beliefs simply co-exist and whichever search surfaces first
                    # becomes the answer. Logging and continuing was MG-029.
                    logger.error(
                        "Failed to link contradiction %s -> %s: %s",
                        memory_id,
                        conflicting_id,
                        exc,
                    )
                    self._quarantine_unlinked_contradiction(
                        memory_id, conflicting_id, reason, exc
                    )
                    raise ContradictionNotRecordedError(
                        f"contradiction between {memory_id} and {conflicting_id} "
                        f"could not be recorded: {exc}"
                    ) from exc

                if self.config.quality.auto_supersede and self._may_supersede(
                    superseding_id=memory_id, superseded_id=conflicting_id
                ):
                    self._supersede_memory(conflicting_id, superseded_by=memory_id, reason=reason)

        return memory_id

    def _may_supersede(self, *, superseding_id: str, superseded_id: str) -> bool:
        """Whether the new belief carries enough authority to retire the old one.

        Recency alone used to decide this: whatever arrived last won. So an
        externally-sourced note could retire an authored prohibition simply by
        being newer, which is the wrong way round for exactly the beliefs that
        matter most (MG-028).

        Authority is the provenance tier's base trust. Equal authority still
        supersedes — that is ordinary belief revision by the same author. Lower
        authority does not; the contradiction is still recorded as an edge, so
        the disagreement stays visible rather than being silently applied.
        """
        from visp_memory.core.trust import TIER_POLICIES, Provenance

        def authority(memory_id: str) -> float:
            record = self._storage.peek_memory(memory_id) or {}
            tier = Provenance.parse(record.get("source"))
            policy = TIER_POLICIES.get(tier)
            return float(getattr(policy, "base_trust", 0.0) or 0.0)

        new_authority = authority(superseding_id)
        old_authority = authority(superseded_id)
        if new_authority >= old_authority:
            return True

        logger.info(
            "Not superseding %s with %s: authority %.2f does not reach %.2f",
            superseded_id,
            superseding_id,
            new_authority,
            old_authority,
        )
        return False

    def _quarantine_unlinked_contradiction(
        self, memory_id: str, conflicting_id: str, reason: str, exc: Exception
    ) -> None:
        """Mark a memory whose contradiction edge could not be written.

        Best effort by design: this runs while already handling a storage
        failure, so it must not raise a second one over the top of the first and
        lose the original cause.
        """
        try:
            existing = self._storage.peek_memory(memory_id) or {}
            self._storage.update_memory(
                memory_id,
                status="quarantined",
                metadata={
                    **(existing.get("metadata") or {}),
                    "contradiction_unrecorded": {
                        "conflicting_id": conflicting_id,
                        "reason": redact_for_storage(str(reason), None)[0],
                        "error": redact_for_storage(str(exc), None)[0],
                    },
                },
            )
        except Exception:  # pragma: no cover - already in a failure path
            logger.exception(
                "Could not quarantine %s after its contradiction edge failed",
                memory_id,
            )

    def _apply_reconcile_decision(
        self,
        decision,
        knowledge: str,
        importance: float,
        *,
        evidence_ids: List[str],
        source_episodes: List[str],
        repo_id: str,
        write_channel: WriteChannel,
    ) -> str:
        """Reinforce or refresh an existing memory instead of inserting a duplicate."""
        from visp_memory.core.clock import utc_now_iso

        existing = self._storage.get_memory(decision.target_id)
        if not existing:  # pragma: no cover - race between decide and apply
            return decision.target_id
        merged_importance = max(float(existing.get("importance", 0.5) or 0.0), float(importance))
        if decision.action == "update":
            revision_evidence_ids = list(dict.fromkeys(evidence_ids or []))
            # A source-episode semantic write normally lets storage resolve lineage
            # through ``source_ids``.  Revisions are a new row, so carry the source
            # rows' Evidence explicitly or the successor would fail the governed
            # evidence requirement (and, worse, reconciliation would behave
            # differently from a first write).
            peek = getattr(self._storage, "peek_memory", None)
            for source_id in source_episodes or []:
                source = (
                    peek(source_id)
                    if callable(peek)
                    else self._storage.get_memory(source_id)
                )
                if source:
                    revision_evidence_ids.extend(source.get("evidence_ids") or [])
            revision_evidence_ids = list(dict.fromkeys(revision_evidence_ids))
            existing_evidence_ids = set(existing.get("evidence_ids") or [])
            if not any(
                evidence_id not in existing_evidence_ids
                for evidence_id in revision_evidence_ids
            ):
                policy = channel_policy(write_channel)
                revision_evidence_ids.append(
                    self._storage.store_evidence(
                        knowledge,
                        repo_id=repo_id,
                        evidence_type="caller_input",
                        provenance=policy.provenance.value,
                        metadata={"write_channel": write_channel.value},
                    )
                )
            successor_id = self._storage.revise_memory(
                decision.target_id,
                knowledge,
                evidence_ids=revision_evidence_ids,
                metadata={
                    **(existing.get("metadata") or {}),
                    "reconciled_at": utc_now_iso(),
                    "reconcile_action": "update",
                },
                importance=merged_importance,
                reason="Write-time reconciliation supplied a more detailed belief",
            )
            logger.info(
                "Reconciled knowledge into successor %s (supersedes %s, overlap=%.2f): %s",
                successor_id,
                decision.target_id,
                decision.similarity,
                decision.reason,
            )
            return successor_id

        updates: Dict[str, Any] = {"importance": merged_importance}
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
        reason, _ = redact_for_storage(str(reason), None)

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

    def check_conflict(
        self,
        content: str,
        layer: str = "semantic",
        repo_id: str = None,
    ) -> "ConflictVerdict":
        """
        Check if content conflicts with existing memories.

        Args:
            content: New content to check
            layer: Layer to check against
            repo_id: Repository to search. Defaults to the configured repository,
                which is only correct when the caller has not scoped the write
                elsewhere — pass the effective repository explicitly.

        Returns:
            A ConflictVerdict. An undetermined verdict is not a clear one.
        """
        content, _ = redact_for_storage(content, None)
        # Search the repository the write is actually going to. Using the
        # configured one meant a write scoped to repo B was checked against
        # repo A, so a contradiction inside repo B was never seen (MG-027).
        search_repo_id = repo_id if repo_id is not None else self.config.repo_id

        relevant = self._storage.search_memories(
            query=content, layer=layer, limit=5, repo_id=search_repo_id
        )

        return self.conflict_detector.detect_conflicts(content, relevant)

    def revise(
        self,
        memory_id: str,
        content: str,
        *,
        evidence_ids: List[str],
        authority_attestation: str = None,
        metadata: Dict[str, Any] = None,
        quality_flags: List[str] = None,
        reason: str = None,
    ) -> str:
        """Create an evidence-backed successor for a semantic belief."""
        content, quality_flags = redact_for_storage(
            content,
            quality_flags,
            reject_if_redacted=authority_attestation is not None,
        )
        return self._storage.revise_memory(
            memory_id,
            content,
            evidence_ids=evidence_ids,
            authority_attestation=authority_attestation,
            metadata=metadata,
            quality_flags=quality_flags,
            reason=reason,
        )

    # Mirror the storage operation name for callers that use the lower-level contract.
    revise_memory = revise

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

    @property
    def embedding_provider_name(self) -> str:
        """The embedding provider actually in use, or None if this process cannot tell.

        Distinct from ``config.embedding.provider``, which records what was asked
        for. When that is ``auto`` and every candidate is unavailable, the answer
        here is ``noop`` -- the difference between the two is the whole of LC-90.
        """
        return self._embedding_provider_name

    @property
    def recall_scores_are_lexical(self) -> bool:
        """Whether ``recall`` scores are keyword overlap rather than vector similarity.

        True when no vectors take part in ranking, so every score returned is
        ``text_similarity`` against the query. Two independent ways to land there,
        and a score is lexical under either: no usable embedding provider, or a
        backend that does not do vector search (ArcadeDB by design; sqlite without
        ChromaDB installed).

        None when this process cannot tell -- client mode ranks on the server -- so
        a caller can decline to label rather than guess. Deliberately never claims
        the converse: False means "not provably lexical", not "semantic".
        """
        if self._embedding_provider_name is None:
            return None
        if produces_no_vectors(self._embedding_provider_name):
            return True
        try:
            return not self._storage.get_capabilities().vector_search
        except Exception:
            return None

    @property
    def lexical_recall_remediation(self) -> str:
        """The repair that would turn on semantic recall here, or None if none applies.

        None for a state the operator chose. Setting the provider to ``none`` is
        documented as the correct way to turn vector search off on a backend that
        runs its own index, and ArcadeDB is lexical by design -- urging those
        operators to install embeddings is advice that cannot work, on every single
        recall. `doctor` already draws this line for the same reason; the surfaces
        that nag have to draw it too, or the nagging is what gets ignored.

        Otherwise the two causes get two answers: someone who already has
        sentence-transformers installed must not be told to install it.
        """
        if self.recall_scores_are_lexical is not True:
            return None
        if is_noop_provider(self.config.embedding.provider):
            return None
        if produces_no_vectors(self._embedding_provider_name):
            return ENABLE_SEMANTIC_RECALL_REMEDIATION
        return ENABLE_VECTOR_INDEX_REMEDIATION

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
        query, _ = redact_for_storage(query, None)
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

        files_in_scope = self._unique_values(file_values)
        graph = self.code_graph(repo_id)
        proximity = graph.structural_proximity(files_in_scope) if graph else {}

        return {
            "repo_id": repo_id,
            "task": task_text,
            "files": files_in_scope,
            "session_id": session_id,
            "constraints": self._unique_values(constraint_values),
            "dependencies": self._unique_values(dependency_values),
            "active_intents": active_intents,
            "file_proximity": proximity,
            "code_graph_snapshot": graph.snapshot_id if graph else None,
        }

    def code_graph(self, repo_id: str = None):
        """Intel's file-grain projection for this repo, or ``None``.

        ``None`` is the normal case and costs nothing: no configured path, no artifact,
        an artifact built against a snapshot that was not the head, or one Memory
        cannot parse all answer the same way, and every caller degrades to the
        behaviour it had before this existed. The last read's verdict is kept on
        ``last_code_graph_load`` so "structure did nothing" always has a reason
        attached rather than being a silent ``None``.
        """
        from visp_memory.core.code_graph import load_for_repo

        load = load_for_repo(self.config, repo_id or self.config.repo_id)
        self.last_code_graph_load = load.as_dict()
        return load.graph

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

        file_score, file_reason = self._file_factor(
            text,
            context.get("files") or [],
            context.get("file_proximity") or {},
        )
        if file_score > 0:
            factors["file"] = {"score": file_score, "reason": file_reason}
            snapshot = context.get("code_graph_snapshot")
            if snapshot and file_score < 0.6:
                # Below the basename tier the only thing that could have produced this
                # score is the projection, so the snapshot that vouched for it is named.
                factors["file"]["code_graph_snapshot"] = snapshot

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
    def _file_factor(
        cls,
        text: str,
        files: List[str],
        proximity: Dict[str, float] = None,
    ) -> tuple[float, str | None]:
        """Score a memory against the files in scope, identity first, structure after.

        The tiers are ordered and the order is the point: exact path 1.0, basename 0.6,
        one import/test hop 0.5, two hops 0.25. Both structural tiers sit strictly
        below both identity tiers, so nothing that ranks above something else today can
        invert tomorrow -- structure only distinguishes memories that used to score a
        flat zero.

        Structural tiers match on the full path only. The basename shortcut is a
        rename-tolerance hack that is tolerable when a human wrote the path into the
        memory and is a false-match generator when a graph supplied it.
        """
        for file_path in files:
            normalized = str(file_path).lower().replace("\\", "/")
            if normalized and normalized in text:
                return 1.0, f"matched file {file_path}"
            basename = Path(normalized).name
            if basename and basename in text:
                return 0.6, f"matched file name {basename}"

        for near_path, score in sorted(
            (proximity or {}).items(), key=lambda item: (-item[1], item[0])
        ):
            normalized = str(near_path).lower().replace("\\", "/")
            if normalized and normalized in text:
                hops = 1 if score >= 0.5 else 2
                return score, (
                    f"matched {near_path}, {hops} import/test hop"
                    f"{'s' if hops != 1 else ''} from the files in scope"
                )
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

    def deduplicate(self, layer: str = "episodic", threshold: float = 0.9) -> DedupReport:
        """Check a layer for duplicate memories.

        Returns:
            A :class:`DedupReport`. An undetermined report means the check could
            not run — it is not a clean bill of health, and callers must not
            render it as one.
        """
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
