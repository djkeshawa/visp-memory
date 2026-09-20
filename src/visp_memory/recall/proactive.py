"""
Proactive Recall Module

Automatically surfaces relevant memories based on context:
- Files being worked on
- Errors encountered
- Directories being modified
"""

import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional

from visp_memory.core.eligibility import (
    EligibilityFilterResult,
    filter_recall_eligible,
    normalize_optional_scope_values,
    require_repo_id,
)
from visp_memory.core.ranking import graph_edge_score
from visp_memory.core.trust import TrustFilterResult, filter_unsolicited

# The intent layer stores its verbs as description prefixes ("WORKING ON: ...").
# They are storage detail, not something an assistant needs to read every turn.
_INTENT_PREFIXES = ("WORKING ON:", "FOCUS:")

# A CONSTRAINT intent says what NOT to do. Rendered under "Current direction" it
# reads as the objective, inverting it, so it is never used as the direction.
_CONSTRAINT_PREFIX = "CONSTRAINT:"

# One line, hard-capped. The injected block is paid for on every turn, so the
# current direction earns its place only if it stays a single short line.
ACTIVE_INTENT_MAX_CHARS = 120

# Distinguishes "not looked up yet" from a genuine "no intent set" (None).
_UNSET = object()

# Outcomes that mean the intent is no longer what the work is aimed at. These are
# non-authoritative history entries - they say what was reported, not that the
# task is finished, and reading them here changes no stored status.
_SETTLED_OUTCOMES = frozenset({"completed", "closed"})
_INJECTION_MEMORY_LIMIT = 4
_RELATED_CANDIDATE_LIMIT = 16
_RELATED_MIN_CONFIDENCE = 0.5
_MEMORY_CONTEXT_KEYS = (
    "warnings",
    "bugs",
    "decisions",
    "knowledge",
    "related",
    "conventions",
    "recent_changes",
    "patterns",
    "recent_activity",
    "history",
)

_MARKDOWN_SECTIONS = (
    ("warnings", "⚠️  **Warnings**"),
    ("bugs", "🐛 **Recent Bugs Fixed**"),
    ("decisions", "📋 **Architectural Decisions**"),
    ("knowledge", "💡 **Relevant Knowledge**"),
    ("related", "🔗 **Related Context**"),
    ("conventions", "📐 **Conventions**"),
    ("recent_changes", "🔄 **Recent Changes**"),
    ("patterns", "🧩 **Patterns**"),
    ("recent_activity", "🕘 **Recent Activity**"),
    ("history", "📜 **History**"),
)


def _has_completion_outcome(intent: Dict[str, Any]) -> bool:
    """True when a completion or close outcome has been recorded against it."""
    context = intent.get("context")
    if not isinstance(context, dict):
        return False
    history = context.get("outcome_history")
    if not isinstance(history, list):
        return False
    return any(
        isinstance(entry, dict) and entry.get("outcome") in _SETTLED_OUTCOMES
        for entry in history
    )


def _strip_intent_prefix(description: str) -> str:
    """Drop the stored prefix; omit oversized direction rather than its conditions."""
    text = description.strip()
    for prefix in _INTENT_PREFIXES:
        if text.upper().startswith(prefix):
            text = text[len(prefix):].strip()
            break
    if len(text) <= ACTIVE_INTENT_MAX_CHARS:
        return text

    return "[omitted: full direction exceeds the automatic injection budget]"


class ProactiveRecall:
    """
    Proactively surface relevant memories without explicit queries.

    Usage:
        recall = ProactiveRecall(memory)

        # Get context for a file
        context = recall.on_file_open("src/auth/login.py")

        # Find similar errors
        similar = recall.on_error("TypeError: Cannot read property 'id' of undefined")

        # Get directory knowledge
        dir_context = recall.on_directory("src/auth/")
    """

    def __init__(
        self,
        memory,
        *,
        repo_id: str = None,
        environment: Any = None,
        task_type: Any = None,
        as_of=None,
    ):
        """
        Initialize proactive recall.

        Args:
            memory: Memory instance to recall from
        """
        self.memory = memory
        self.repo_id = require_repo_id(repo_id or memory.config.repo_id)
        self.environment = list(
            normalize_optional_scope_values(environment, field="environment")
        ) or None
        self.task_type = list(
            normalize_optional_scope_values(task_type, field="task_type")
        ) or None
        self.as_of = as_of
        self.last_trust_filter = TrustFilterResult.combine([]).diagnostics()
        self.last_eligibility_filter = EligibilityFilterResult.combine([]).diagnostics()
        self._active_intent: Any = _UNSET

    def _runtime_scope(self) -> Dict[str, Any]:
        return {
            "environment": self.environment,
            "task_type": self.task_type,
            "as_of": self.as_of,
        }

    def _trusted(
        self,
        memories: List[Dict[str, Any]],
        reports: List[TrustFilterResult],
        eligibility_reports: List[EligibilityFilterResult],
    ) -> List[Dict[str, Any]]:
        eligibility = filter_recall_eligible(
            memories,
            repo_id=self.repo_id,
            environment=self.environment,
            task_type=self.task_type,
            as_of=self.as_of,
        )
        eligibility_reports.append(eligibility)
        result = filter_unsolicited(eligibility.allowed)
        reports.append(result)
        return result.allowed

    def on_file_open(
        self, file_path: str, include_related: bool = True
    ) -> Dict[str, Any]:
        """
        Get relevant memories when opening a file.

        Returns warnings, recent bugs, decisions, and patterns
        related to this file.

        Args:
            file_path: Path to file being opened
            include_related: Also include memories from related files

        Returns:
            Dict with 'warnings', 'bugs', 'decisions', 'knowledge' and
            'active_intent' keys
        """
        file_path = self._normalize_path(file_path)
        repo_id = self.repo_id
        trust_results: List[TrustFilterResult] = []
        eligibility_results: List[EligibilityFilterResult] = []

        results = {
            "warnings": [],
            "bugs": [],
            "decisions": [],
            "knowledge": [],
            "related": [],
            "recent_changes": [],
        }

        # Get file-specific warnings
        warnings = self._trusted(
            self.memory.semantic.get_warnings(file_path, repo_id=repo_id),
            trust_results,
            eligibility_results,
        )
        results["warnings"] = self.memory.rank_with_context(
            warnings,
            query=file_path,
            repo_id=repo_id,
            files=[file_path],
            limit=len(warnings) or None,
            min_score=None,
        )

        # Search for bugs in this file
        bug_query = f"file:{file_path} bug"
        bug_results = self._trusted(
            self.memory.episodic.search(
                query=bug_query,
                category="bug_fixed",
                limit=5,
                repo_id=repo_id,
                **self._runtime_scope(),
            ),
            trust_results,
            eligibility_results,
        )
        results["bugs"] = self.memory.rank_with_context(
            bug_results,
            query=bug_query,
            repo_id=repo_id,
            files=[file_path],
            limit=5,
            min_score=None,
        )

        # Search for decisions affecting this file
        decision_results = self._trusted(
            self.memory.episodic.search(
                query=file_path,
                category="architecture_decision",
                limit=3,
                repo_id=repo_id,
                **self._runtime_scope(),
            ),
            trust_results,
            eligibility_results,
        )
        results["decisions"] = self.memory.rank_with_context(
            decision_results,
            query=file_path,
            repo_id=repo_id,
            files=[file_path],
            limit=3,
            min_score=None,
        )

        # Get general knowledge about this file/module
        knowledge_results = self._trusted(
            self.memory.semantic.relevant_for(
                files=[file_path],
                limit=5,
                repo_id=repo_id,
                **self._runtime_scope(),
            ),
            trust_results,
            eligibility_results,
        )
        results["knowledge"] = self.memory.rank_with_context(
            knowledge_results,
            query=file_path,
            repo_id=repo_id,
            files=[file_path],
            limit=5,
            min_score=None,
        )

        # Get recent changes to this file (from git capture)
        recent = self._trusted(
            self.memory.episodic.search(
                query=file_path,
                limit=3,
                repo_id=repo_id,
                **self._runtime_scope(),
            ),
            trust_results,
            eligibility_results,
        )
        recent_changes = [
            r for r in recent if file_path.lower() in r.get("content", "").lower()
        ]
        results["recent_changes"] = self.memory.rank_with_context(
            recent_changes,
            query=file_path,
            repo_id=repo_id,
            files=[file_path],
            limit=3,
            min_score=None,
        )

        if include_related:
            direct_ids = list(
                dict.fromkeys(
                    item.get("id")
                    for key in (
                        "warnings",
                        "bugs",
                        "decisions",
                        "knowledge",
                        "recent_changes",
                    )
                    for item in results[key]
                    if item.get("id")
                )
            )
            related_candidates: List[Dict[str, Any]] = []
            seen_related = set(direct_ids)
            for source_id in direct_ids:
                for item in self.memory._storage.get_related_memories(source_id):
                    related_id = item.get("id")
                    if not related_id or related_id in seen_related:
                        continue
                    if graph_edge_score(
                        item.get("strength"), item.get("relationship_evidence")
                    ) < _RELATED_MIN_CONFIDENCE:
                        continue
                    seen_related.add(related_id)
                    related_candidates.append(item)
                    if len(related_candidates) >= _RELATED_CANDIDATE_LIMIT:
                        break
                if len(related_candidates) >= _RELATED_CANDIDATE_LIMIT:
                    break

            trusted_related = self._trusted(
                related_candidates,
                trust_results,
                eligibility_results,
            )
            results["related"] = self.memory.rank_with_context(
                trusted_related,
                query=file_path,
                repo_id=repo_id,
                files=[file_path],
                limit=_INJECTION_MEMORY_LIMIT,
                min_score=None,
            )

        results["active_intent"] = self.active_intent()

        self.last_trust_filter = TrustFilterResult.combine(trust_results).diagnostics()
        self.last_eligibility_filter = EligibilityFilterResult.combine(
            eligibility_results
        ).diagnostics()
        results["trust_filter"] = self.last_trust_filter
        results["eligibility_filter"] = self.last_eligibility_filter
        return results

    def for_files(self, files: List[str]) -> Dict[str, Any]:
        """Context for a set of files, aggregated into one injectable block.

        The per-file loop lived in two places - the CLI's `inject` and the hook
        adapters - as byte-identical copies, and when the active intent was
        added only one of the copies learned about it. Keeping one
        implementation is what stops the two surfaces answering differently.

        The active intent is a property of the session rather than of any one
        file, so it is taken once instead of accumulated.
        """
        if len(files) == 1:
            return self.on_file_open(files[0])

        aggregated: Dict[str, Any] = {
            "warnings": [],
            "bugs": [],
            "decisions": [],
            "knowledge": [],
            "related": [],
        }
        for file in files:
            file_context = self.on_file_open(file)
            for key in aggregated:
                aggregated[key].extend(file_context.get(key, []))
        aggregated["active_intent"] = self.active_intent()
        return aggregated

    def active_intent(self) -> Optional[str]:
        """The one-line current direction, or None when no intent is set.

        The live task wins over a standing focus: an assistant that is told both
        needs the narrower one. Returns the description only - this is direction,
        never permission, and the caller renders it as a single line so the
        injected block stays inside its token budget.

        Computed once per instance: injecting for ten files must not re-read the
        intent layer ten times to produce one line.
        """
        if self._active_intent is _UNSET:
            self._active_intent = self._read_active_intent()
        return self._active_intent

    def _read_active_intent(self) -> Optional[str]:
        """Pick the intent that describes what the work is aimed at right now.

        Skips intents that already carry a recorded completion outcome. Intent
        *status* is externally owned and `done` deliberately leaves it alone, so
        without this the block would keep leading with a task the user has
        already reported finished, with no way to clear it.
        """
        try:
            intents = self.memory.intent.get_active(repo_id=self.repo_id)
        except Exception:
            return None

        live = [
            intent
            for intent in intents or []
            if not _has_completion_outcome(intent)
            and not str(intent.get("description", "")).upper().startswith(_CONSTRAINT_PREFIX)
        ]
        if not live:
            return None

        for prefix in _INTENT_PREFIXES:
            for intent in live:
                description = str(intent.get("description", ""))
                if description.upper().startswith(prefix):
                    return _strip_intent_prefix(description)

        return _strip_intent_prefix(str(live[0].get("description", ""))) or None

    def on_error(
        self, error_message: str, error_type: str = None, file_path: str = None, limit: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Find similar errors that occurred in the past.

        Ranks by whatever the active embedding provider supports: vector
        similarity where one is configured, keyword overlap otherwise. The
        `similarity` field it returns means different things in those two
        cases, so a caller that displays the number must label it -- see
        `Memory.recall_scores_are_lexical`.

        Args:
            error_message: The error message text
            error_type: Optional error type (e.g., "TypeError", "ValueError")
            file_path: Optional file where error occurred
            limit: Maximum similar errors to return

        Returns:
            List of similar past errors with fixes
        """
        # Build search query
        query_parts = [error_message]
        if error_type:
            query_parts.insert(0, error_type)
        if file_path:
            query_parts.append(self._normalize_path(file_path))

        query = " ".join(query_parts)

        # Search for similar bugs and known issues
        repo_id = self.repo_id
        files = [self._normalize_path(file_path)] if file_path else None
        trust_results: List[TrustFilterResult] = []
        eligibility_results: List[EligibilityFilterResult] = []
        similar_bugs = self._trusted(
            self.memory.episodic.search(
                query=query,
                category="bug_fixed",
                limit=limit,
                repo_id=repo_id,
                **self._runtime_scope(),
            ),
            trust_results,
            eligibility_results,
        )

        similar_issues = self._trusted(
            self.memory.semantic.search(
                query=query,
                category="negative",
                limit=limit,
                repo_id=repo_id,
                **self._runtime_scope(),
            ),
            trust_results,
            eligibility_results,
        )

        # Combine and deduplicate
        all_results = similar_bugs + similar_issues

        ranked = self.memory.rank_with_context(
            all_results,
            query=query,
            repo_id=repo_id,
            task=error_type,
            files=files,
            limit=limit,
            min_score=None,
        )
        self.last_trust_filter = TrustFilterResult.combine(trust_results).diagnostics()
        self.last_eligibility_filter = EligibilityFilterResult.combine(
            eligibility_results
        ).diagnostics()
        return ranked

    def on_directory(
        self, dir_path: str, recursive: bool = False
    ) -> Dict[str, Any]:
        """
        Aggregate knowledge for an entire directory.

        Returns consolidated warnings, patterns, and conventions
        for all files in the directory.

        Args:
            dir_path: Directory path
            recursive: Include subdirectories

        Returns:
            Aggregated context for directory
        """
        dir_path = self._normalize_path(dir_path)
        # ``_normalize_path`` preserves case, but content/metadata are matched
        # case-insensitively, so compare against a lowercased key.
        dir_key = dir_path.lower()
        repo_id = self.repo_id
        trust_results: List[TrustFilterResult] = []
        eligibility_results: List[EligibilityFilterResult] = []

        results = {
            "warnings": [],
            "conventions": [],
            "patterns": [],
            "recent_activity": [],
            "active_intent": self.active_intent(),
        }

        # Get all warnings mentioning this directory
        all_warnings = self._trusted(
            self.memory.semantic.get_warnings(repo_id=repo_id),
            trust_results,
            eligibility_results,
        )
        dir_warnings = [
            w
            for w in all_warnings
            if dir_key in w.get("content", "").lower()
            or dir_key in str((w.get("metadata") or {}).get("applies_to", [])).lower()
        ]
        results["warnings"] = self.memory.rank_with_context(
            dir_warnings,
            query=dir_path,
            repo_id=repo_id,
            files=[dir_path],
            limit=len(dir_warnings) or None,
            min_score=None,
        )

        # Get conventions for this directory
        all_conventions = self._trusted(
            self.memory.semantic.get_conventions(repo_id=repo_id),
            trust_results,
            eligibility_results,
        )
        dir_conventions = [c for c in all_conventions if dir_key in c.get("content", "").lower()]
        results["conventions"] = self.memory.rank_with_context(
            dir_conventions,
            query=dir_path,
            repo_id=repo_id,
            files=[dir_path],
            limit=len(dir_conventions) or None,
            min_score=None,
        )

        # Search for patterns in this directory
        pattern_results = self._trusted(
            self.memory.semantic.search(
                query=dir_path,
                category="procedure",
                limit=10,
                repo_id=repo_id,
                **self._runtime_scope(),
            ),
            trust_results,
            eligibility_results,
        )
        results["patterns"] = self.memory.rank_with_context(
            pattern_results,
            query=dir_path,
            repo_id=repo_id,
            files=[dir_path],
            limit=10,
            min_score=None,
        )

        # Get recent activity in this directory
        recent = self._trusted(
            self.memory.episodic.recent(limit=20, repo_id=repo_id),
            trust_results,
            eligibility_results,
        )
        dir_recent = [r for r in recent if dir_key in r.get("content", "").lower()]
        results["recent_activity"] = self.memory.rank_with_context(
            dir_recent,
            query=dir_path,
            repo_id=repo_id,
            files=[dir_path],
            limit=5,
            min_score=None,
        )

        self.last_trust_filter = TrustFilterResult.combine(trust_results).diagnostics()
        self.last_eligibility_filter = EligibilityFilterResult.combine(
            eligibility_results
        ).diagnostics()
        results["trust_filter"] = self.last_trust_filter
        results["eligibility_filter"] = self.last_eligibility_filter
        return results

    def format_injection(
        self,
        memories: Dict[str, List[Dict[str, Any]]],
        format: str = "markdown",
        max_length: int = 2000,
    ) -> str:
        """
        Format memories for LLM context injection.

        Args:
            memories: Dict of memory lists (from on_file_open, etc.)
            format: Output format ("markdown", "plain", "json")
            max_length: Maximum character length

        Returns:
            Formatted string for injection
        """
        original_memories = memories
        memories = self._budget_injection_memories(memories)
        omitted = self._injection_omissions(original_memories, memories)

        if format == "json":
            return self._format_json_injection(memories, max_length, omitted)

        return self._format_markdown_injection(memories, max_length, omitted)

    @staticmethod
    def _injection_omissions(original: Dict[str, Any], budgeted: Dict[str, Any]) -> Dict[str, int]:
        """Count complete memory units removed by the automatic four-item gate."""
        omitted = {}
        for key in _MEMORY_CONTEXT_KEYS:
            original_values = original.get(key)
            kept_values = budgeted.get(key)
            if isinstance(original_values, list) and isinstance(kept_values, list):
                count = max(0, len(original_values) - len(kept_values))
                if count:
                    omitted[key] = count
        return omitted

    @staticmethod
    def _json_dump(value: Dict[str, Any], *, pretty: bool = False) -> str:
        import json

        return json.dumps(
            value,
            indent=2 if pretty else None,
            separators=None if pretty else (",", ":"),
            default=str,
        )

    @classmethod
    def _json_payload(
        cls, values: Dict[str, Any], omitted: Dict[str, int]
    ) -> Dict[str, Any]:
        payload = dict(values)
        if omitted:
            payload["truncated"] = True
            payload["omitted"] = dict(omitted)
        return payload

    @classmethod
    def _format_json_injection(
        cls, memories: Dict[str, Any], max_length: int, omitted: Dict[str, int]
    ) -> str:
        """Pack whole JSON evidence units and retain a parseable envelope.

        JSON callers must provide at least two characters, the size of the
        smallest valid object (``{}``).
        """
        if max_length < 2:
            raise ValueError("max_length must be at least 2 for JSON injection")

        full = cls._json_dump(cls._json_payload(memories, omitted), pretty=True)
        if len(full) <= max_length:
            return full

        # Diagnostics and other non-evidence fields can be large. During
        # truncation retain the public memory sections and current direction;
        # the omitted map explains why some units are absent.
        packed: Dict[str, Any] = {
            key: [] for key in _MEMORY_CONTEXT_KEYS if isinstance(memories.get(key), list)
        }
        if "active_intent" in memories:
            packed["active_intent"] = memories["active_intent"]
        counts = dict(omitted)
        for key in _MEMORY_CONTEXT_KEYS:
            values = memories.get(key)
            if not isinstance(values, list):
                continue
            for item in values:
                trial = {**packed, key: [*packed[key], item]}
                rendered = cls._json_dump(cls._json_payload(trial, counts))
                if len(rendered) <= max_length:
                    packed = trial
                else:
                    counts[key] = counts.get(key, 0) + 1

        result = cls._json_dump(cls._json_payload(packed, counts))
        while len(result) > max_length:
            removable = next(
                (key for key in reversed(_MEMORY_CONTEXT_KEYS) if packed.get(key)), None
            )
            if removable is None:
                break
            packed[removable].pop()
            counts[removable] = counts.get(removable, 0) + 1
            result = cls._json_dump(cls._json_payload(packed, counts))
        if len(result) <= max_length:
            return result

        # Empty sections are useful at normal sizes but are expendable when the
        # response envelope itself is the limiting factor.
        compact = {key: value for key, value in packed.items() if value not in ([], None, "")}
        result = cls._json_dump(cls._json_payload(compact, counts))
        if len(result) <= max_length:
            return result
        for fallback in ({"truncated": True}, {}):
            result = cls._json_dump(fallback)
            if len(result) <= max_length:
                return result
        return ""

    @staticmethod
    def _markdown_omission_marker(omitted: int, max_length: int | None = None) -> str:
        suffix = "s" if omitted != 1 else ""
        marker = f"⚠️ Evidence omitted ({omitted} item{suffix}) to fit max_length."
        if max_length is None or len(marker) <= max_length:
            return marker
        compact = f"… {omitted} omitted"
        if len(compact) <= max_length:
            return compact
        return "…" if max_length else ""

    @classmethod
    def _format_markdown_injection(
        cls, memories: Dict[str, Any], max_length: int, omitted: Dict[str, int]
    ) -> str:
        """Render complete evidence units; never clip a fact or qualification."""
        if max_length <= 0:
            return ""
        blocks: list[str] = []
        selected_sections: set[str] = set()
        total_omitted = sum(omitted.values())

        active_intent = memories.get("active_intent")
        if active_intent:
            intent = f"\U0001F3AF **Current direction**: {active_intent}"
            marker = cls._markdown_omission_marker(total_omitted + 1, max_length)
            if len(intent) + (len(marker) + 2 if total_omitted else 0) <= max_length:
                blocks.append(intent)
            else:
                total_omitted += 1

        for key, heading in _MARKDOWN_SECTIONS:
            values = memories.get(key)
            if not isinstance(values, list):
                continue
            for item in values:
                if not isinstance(item, dict):
                    total_omitted += 1
                    continue
                content = str(item.get("content", "")).strip()
                if key == "warnings":
                    content = content.removeprefix("WARNING ").strip()
                if not content:
                    total_omitted += 1
                    continue
                prefix = heading if key not in selected_sections else ""
                citation = f"[{item['id']}] " if item.get("id") else ""
                unit = f"{prefix + chr(10) if prefix else ''}  • {citation}{content}"
                candidate = "\n\n".join([*blocks, unit])
                marker = cls._markdown_omission_marker(total_omitted + 1, max_length)
                if len(candidate) + (len(marker) + 2 if total_omitted else 0) <= max_length:
                    blocks.append(unit)
                    selected_sections.add(key)
                else:
                    total_omitted += 1

        output = "\n\n".join(blocks)
        if total_omitted:
            marker = cls._markdown_omission_marker(total_omitted, max_length)
            output = "\n\n".join([part for part in (output, marker) if part])
            while len(output) > max_length and blocks:
                blocks.pop()
                total_omitted += 1
                marker = cls._markdown_omission_marker(total_omitted, max_length)
                output = "\n\n".join([*blocks, marker])
            if len(output) > max_length:
                output = cls._markdown_omission_marker(total_omitted, max_length)
        return output

    @staticmethod
    def _budget_injection_memories(
        context: Dict[str, Any], limit: int = _INJECTION_MEMORY_LIMIT
    ) -> Dict[str, Any]:
        """Return a de-duplicated copy with one budget across every memory section."""
        budgeted = dict(context)
        seen_ids = set()
        selected = 0
        for key in _MEMORY_CONTEXT_KEYS:
            values = context.get(key)
            if not isinstance(values, list):
                continue
            kept = []
            for item in values:
                if not isinstance(item, dict):
                    continue
                memory_id = item.get("id")
                dedupe_key = memory_id or (item.get("layer"), item.get("content"))
                if dedupe_key in seen_ids:
                    continue
                if selected >= limit:
                    break
                seen_ids.add(dedupe_key)
                kept.append(item)
                selected += 1
            budgeted[key] = kept
        return budgeted

    def find_relevant_for_task(
        self, task_description: str, files: List[str] = None, limit: int = 10
    ) -> Dict[str, Any]:
        """
        Find all relevant memories for a specific task.

        Combines file-based lookup with ranked recall to get
        comprehensive context. The ranking is semantic only where an embedding
        provider is active; otherwise it is keyword overlap.

        Args:
            task_description: What task is being worked on
            files: Optional list of files involved
            limit: Max memories per category

        Returns:
            Comprehensive context for task
        """
        results = {"warnings": [], "knowledge": [], "history": [], "patterns": []}
        trust_results: List[TrustFilterResult] = []
        eligibility_results: List[EligibilityFilterResult] = []

        # Get file-specific context if files provided
        if files:
            for file in files:
                file_context = self.on_file_open(file, include_related=False)
                results["warnings"].extend(file_context["warnings"])
                results["knowledge"].extend(file_context["knowledge"])

        # Semantic search for task
        task_results = self.memory.recall(
            query=task_description,
            limit=limit,
            repo_id=self.repo_id,
            environment=self.environment,
            task_type=self.task_type,
            as_of=self.as_of,
        )
        recall_eligibility = getattr(self.memory, "last_recall_eligibility_result", None)
        if recall_eligibility is not None:
            eligibility_results.append(recall_eligibility)
        task_trust = filter_unsolicited(task_results)
        trust_results.append(task_trust)
        task_results = task_trust.allowed

        # Categorize results
        for result in task_results:
            layer = result.get("layer", "")
            category = result.get("category", "")

            if category == "negative":
                results["warnings"].append(result)
            elif layer == "semantic":
                results["knowledge"].append(result)
            elif layer == "episodic":
                results["history"].append(result)

        # Search for patterns
        pattern_results = self.memory.semantic.search(
            query=task_description,
            category="procedure",
            limit=5,
            repo_id=self.repo_id,
            **self._runtime_scope(),
        )
        results["patterns"] = self._trusted(
            pattern_results, trust_results, eligibility_results
        )

        # Deduplicate by ID
        for key in ("warnings", "knowledge", "history", "patterns"):
            seen = set()
            deduped = []
            for item in results[key]:
                if item["id"] not in seen:
                    seen.add(item["id"])
                    deduped.append(item)
            results[key] = deduped[:limit]

        results["active_intent"] = self.active_intent()

        self.last_trust_filter = TrustFilterResult.combine(trust_results).diagnostics()
        self.last_eligibility_filter = EligibilityFilterResult.combine(
            eligibility_results
        ).diagnostics()
        results["trust_filter"] = self.last_trust_filter
        results["eligibility_filter"] = self.last_eligibility_filter

        return results

    def generate_error_hash(self, error_message: str) -> str:
        """
        Generate a stable hash for an error message.

        Useful for tracking recurring errors.

        Args:
            error_message: Error message text

        Returns:
            Short hash string
        """
        # Normalize: remove numbers, paths, specific values
        import re

        normalized = error_message.lower()

        # Remove file paths
        normalized = re.sub(r"[/\\][^\s]+", "<path>", normalized)

        # Remove line numbers
        normalized = re.sub(r"line \d+", "line <n>", normalized)

        # Remove specific values in quotes
        normalized = re.sub(r'["\']([^"\']+)["\']', "<value>", normalized)

        # Remove numbers
        normalized = re.sub(r"\b\d+\b", "<n>", normalized)

        # Generate hash
        return hashlib.sha256(normalized.encode()).hexdigest()[:8]

    # =========================================================================
    # Private Methods
    # =========================================================================

    def _normalize_path(self, path: str) -> str:
        """Normalize file path for consistent matching."""
        # Convert to Path and get relative or name
        p = Path(path)

        # Try to make relative to cwd
        try:
            cwd = Path.cwd()
            if p.is_absolute():
                rel = p.relative_to(cwd)
                return str(rel)
        except ValueError:
            pass

        # Just use the path as-is
        return str(p).replace("\\", "/")
