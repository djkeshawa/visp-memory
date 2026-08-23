"""
Proactive Recall Module

Automatically surfaces relevant memories based on context:
- Files being worked on
- Errors encountered
- Directories being modified
"""

import hashlib
from pathlib import Path
from typing import Any, Dict, List

from visp_memory.core.eligibility import (
    EligibilityFilterResult,
    filter_recall_eligible,
    normalize_optional_scope_values,
    require_repo_id,
)
from visp_memory.core.trust import TrustFilterResult, filter_unsolicited


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

    def __init__(self, memory, *, environment: Any = None, task_type: Any = None, as_of=None):
        """
        Initialize proactive recall.

        Args:
            memory: Memory instance to recall from
        """
        self.memory = memory
        self.repo_id = require_repo_id(memory.config.repo_id)
        self.environment = list(
            normalize_optional_scope_values(environment, field="environment")
        ) or None
        self.task_type = list(
            normalize_optional_scope_values(task_type, field="task_type")
        ) or None
        self.as_of = as_of
        self.last_trust_filter = TrustFilterResult.combine([]).diagnostics()
        self.last_eligibility_filter = EligibilityFilterResult.combine([]).diagnostics()

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
            Dict with 'warnings', 'bugs', 'decisions', 'knowledge' keys
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

        self.last_trust_filter = TrustFilterResult.combine(trust_results).diagnostics()
        self.last_eligibility_filter = EligibilityFilterResult.combine(
            eligibility_results
        ).diagnostics()
        results["trust_filter"] = self.last_trust_filter
        results["eligibility_filter"] = self.last_eligibility_filter
        return results

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

        results = {"warnings": [], "conventions": [], "patterns": [], "recent_activity": []}

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
        if format == "json":
            import json

            return json.dumps(memories, indent=2, default=str)[:max_length]

        lines = []

        # Add warnings (highest priority)
        if memories.get("warnings"):
            lines.append("⚠️  **Warnings**")
            for w in memories["warnings"][:3]:
                content = w["content"].replace("WARNING ", "").strip()
                lines.append(f"  • {content[:150]}")
            lines.append("")

        # Add recent bugs/fixes
        if memories.get("bugs"):
            lines.append("🐛 **Recent Bugs Fixed**")
            for b in memories["bugs"][:2]:
                content = b["content"][:150]
                lines.append(f"  • {content}")
            lines.append("")

        # Add relevant decisions
        if memories.get("decisions"):
            lines.append("📋 **Architectural Decisions**")
            for d in memories["decisions"][:2]:
                content = d["content"].split("\n")[0][:150]
                lines.append(f"  • {content}")
            lines.append("")

        # Add knowledge/patterns
        if memories.get("knowledge"):
            lines.append("💡 **Relevant Knowledge**")
            for k in memories["knowledge"][:3]:
                content = k["content"][:150]
                lines.append(f"  • {content}")
            lines.append("")

        # Add conventions
        if memories.get("conventions"):
            lines.append("📐 **Conventions**")
            for c in memories["conventions"][:2]:
                content = c["content"][:150]
                lines.append(f"  • {content}")
            lines.append("")

        # Add recent changes
        if memories.get("recent_changes"):
            lines.append("🔄 **Recent Changes**")
            for r in memories["recent_changes"][:2]:
                content = r["content"][:150]
                lines.append(f"  • {content}")
            lines.append("")

        output = "\n".join(lines)

        # Truncate if too long
        if len(output) > max_length:
            output = output[: max_length - 100] + "\n\n... (truncated for length)"

        return output

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
