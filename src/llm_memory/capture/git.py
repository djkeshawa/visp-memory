"""
Git Capture Module

Automatically captures memories from git activity:
- Commits (parse messages, analyze diffs)
- Merges (summarize branch work)
- Branch activity

Supports:
- Conventional commit parsing
- Git hook installation
- Historical sync
"""

import re
import subprocess
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime
from enum import Enum

try:
    import git
    GIT_AVAILABLE = True
except ImportError:
    GIT_AVAILABLE = False


class CommitType(str, Enum):
    """Conventional commit types mapped to episode categories."""
    FEAT = "feature_added"
    FIX = "bug_fixed"
    REFACTOR = "refactor"
    DOCS = "note"
    TEST = "note"
    CHORE = "note"
    PERF = "refactor"
    STYLE = "refactor"
    BUILD = "note"
    CI = "note"
    REVERT = "note"


class GitCapture:
    """
    Capture memories from git repository activity.

    Usage:
        capture = GitCapture(memory, repo_path=".")

        # Manual capture
        capture.on_commit("HEAD")

        # Install hooks for automatic capture
        capture.install_hooks()

        # Sync from history
        capture.sync_history(since="1 week ago", limit=50)
    """

    def __init__(self, memory, repo_path: Path = None):
        """
        Initialize git capture.

        Args:
            memory: Memory instance to record to
            repo_path: Path to git repository (defaults to cwd)
        """
        if not GIT_AVAILABLE:
            raise ImportError(
                "GitPython required for git capture. "
                "Install with: pip install llm-memory[capture]"
            )

        self.memory = memory
        self.repo_path = Path(repo_path or Path.cwd())

        try:
            self.repo = git.Repo(self.repo_path, search_parent_directories=True)
        except git.InvalidGitRepositoryError:
            raise ValueError(f"Not a git repository: {self.repo_path}")

    def on_commit(self, commit_ref: str = "HEAD") -> Optional[str]:
        """
        Capture a commit as episodic memory.

        Args:
            commit_ref: Git commit reference (hash, HEAD, etc.)

        Returns:
            Memory ID if captured, None if skipped
        """
        commit = self.repo.commit(commit_ref)

        # Parse commit message
        message = commit.message.strip()
        category, clean_message = self._parse_commit_message(message)

        # Analyze diff to understand what changed
        diff_summary = self._analyze_commit_diff(commit)

        # Build memory content
        content = self._format_commit_memory(
            message=clean_message,
            author=commit.author.name,
            date=commit.committed_datetime,
            diff_summary=diff_summary
        )

        # Calculate importance based on size and type
        importance = self._calculate_commit_importance(commit, category)

        # Record the memory
        memory_id = self.memory.record(
            event=content,
            category=category,
            importance=importance,
            context={
                "commit_hash": commit.hexsha,
                "author": commit.author.name,
                "date": commit.committed_datetime.isoformat(),
                "files_changed": len(commit.stats.files),
                "insertions": commit.stats.total["insertions"],
                "deletions": commit.stats.total["deletions"]
            },
            tags=["git", "auto-captured"]
        )

        return memory_id

    def on_merge(self, branch: str = None) -> Optional[str]:
        """
        Capture a merge as episodic memory with branch summary.

        Args:
            branch: Branch that was merged (auto-detected if None)

        Returns:
            Memory ID if captured
        """
        # Get the merge commit
        merge_commit = self.repo.head.commit

        if not merge_commit.parents or len(merge_commit.parents) < 2:
            return None  # Not a merge commit

        # Get branch name from commit message if not provided
        if not branch:
            branch = self._extract_branch_from_merge(merge_commit.message)

        # Get commits from the merged branch
        base = merge_commit.parents[0]
        merged = merge_commit.parents[1]

        commits = list(self.repo.iter_commits(f"{base}..{merged}"))

        # Summarize the branch work
        summary = self._summarize_branch_work(commits, branch)

        # Record the merge
        memory_id = self.memory.record(
            event=f"Merged branch '{branch}': {summary}",
            category="feature_added",
            importance=0.7,
            context={
                "merge_commit": merge_commit.hexsha,
                "branch": branch,
                "commits_merged": len(commits)
            },
            tags=["git", "merge", "auto-captured"]
        )

        return memory_id

    def sync_history(
        self,
        since: str = None,
        until: str = None,
        limit: int = 100
    ) -> List[str]:
        """
        Sync memories from git history.

        Args:
            since: Date/time to start from (e.g., "1 week ago", "2024-01-01")
            until: Date/time to end at
            limit: Maximum commits to process

        Returns:
            List of created memory IDs
        """
        memory_ids = []

        # Build git log arguments
        kwargs = {"max_count": limit}
        if since:
            kwargs["since"] = since
        if until:
            kwargs["until"] = until

        commits = list(self.repo.iter_commits(**kwargs))

        for commit in commits:
            # Skip merge commits in history sync (they're noisy)
            if len(commit.parents) > 1:
                continue

            try:
                memory_id = self.on_commit(commit.hexsha)
                if memory_id:
                    memory_ids.append(memory_id)
            except Exception as e:
                # Log but don't fail on individual commits
                print(f"Warning: Failed to capture {commit.hexsha[:7]}: {e}")
                continue

        return memory_ids

    def install_hooks(self) -> Dict[str, bool]:
        """
        Install git hooks for automatic capture.

        Installs:
        - post-commit: Capture commits
        - post-merge: Capture merges

        Returns:
            Dict mapping hook name to success status
        """
        hooks_dir = self.repo.git_dir / "hooks"
        hooks_dir = Path(hooks_dir)
        hooks_dir.mkdir(exist_ok=True)

        results = {}

        # Post-commit hook
        post_commit = hooks_dir / "post-commit"
        results["post-commit"] = self._install_hook(
            post_commit,
            self._generate_post_commit_script()
        )

        # Post-merge hook
        post_merge = hooks_dir / "post-merge"
        results["post-merge"] = self._install_hook(
            post_merge,
            self._generate_post_merge_script()
        )

        return results

    def uninstall_hooks(self) -> Dict[str, bool]:
        """
        Remove installed git hooks.

        Returns:
            Dict mapping hook name to success status
        """
        hooks_dir = Path(self.repo.git_dir) / "hooks"
        results = {}

        for hook_name in ["post-commit", "post-merge"]:
            hook_path = hooks_dir / hook_name

            if hook_path.exists():
                # Check if it's our hook
                content = hook_path.read_text()
                if "llm-memory capture" in content:
                    hook_path.unlink()
                    results[hook_name] = True
                else:
                    results[hook_name] = False  # Not our hook
            else:
                results[hook_name] = True  # Already removed

        return results

    # =========================================================================
    # Private Methods
    # =========================================================================

    def _parse_commit_message(self, message: str) -> tuple[str, str]:
        """
        Parse commit message to extract type and clean message.

        Supports conventional commits: type(scope): message

        Returns:
            (category, clean_message)
        """
        # Try conventional commit format
        match = re.match(r'^(\w+)(?:\([^)]+\))?: (.+)', message)

        if match:
            commit_type = match.group(1).upper()
            clean_message = match.group(2)

            # Map to episode category
            category = CommitType.__members__.get(
                commit_type,
                CommitType.FEAT
            ).value
        else:
            # No conventional format, infer from keywords
            clean_message = message.split('\n')[0]  # First line
            category = self._infer_category(clean_message)

        return category, clean_message

    def _infer_category(self, message: str) -> str:
        """Infer category from commit message keywords."""
        message_lower = message.lower()

        if any(word in message_lower for word in ["fix", "bug", "issue", "patch"]):
            return "bug_fixed"
        elif any(word in message_lower for word in ["refactor", "cleanup", "reorganize"]):
            return "refactor"
        elif any(word in message_lower for word in ["add", "implement", "create", "new"]):
            return "feature_added"
        else:
            return "note"

    def _analyze_commit_diff(self, commit) -> Dict[str, Any]:
        """
        Analyze commit diff to understand what changed.

        Returns:
            Summary dict with files, changes, etc.
        """
        stats = commit.stats

        files = list(stats.files.keys())

        # Categorize files
        categorized = {
            "code": [],
            "tests": [],
            "docs": [],
            "config": []
        }

        for file in files:
            if "test" in file.lower():
                categorized["tests"].append(file)
            elif file.endswith((".md", ".txt", ".rst")):
                categorized["docs"].append(file)
            elif file.endswith((".yaml", ".yml", ".json", ".toml", ".ini", ".cfg")):
                categorized["config"].append(file)
            else:
                categorized["code"].append(file)

        return {
            "files_changed": len(files),
            "insertions": stats.total["insertions"],
            "deletions": stats.total["deletions"],
            "categorized_files": categorized
        }

    def _format_commit_memory(
        self,
        message: str,
        author: str,
        date: datetime,
        diff_summary: Dict[str, Any]
    ) -> str:
        """Format commit info as memory content."""
        parts = [f"Commit: {message}"]

        # Add file context if significant
        files = diff_summary["categorized_files"]
        if files["code"]:
            parts.append(f"Modified: {', '.join(files['code'][:5])}")
            if len(files["code"]) > 5:
                parts.append(f"... and {len(files['code']) - 5} more files")

        return " | ".join(parts)

    def _calculate_commit_importance(self, commit, category: str) -> float:
        """Calculate importance score for commit."""
        base_importance = {
            "bug_fixed": 0.7,
            "feature_added": 0.6,
            "refactor": 0.4,
            "note": 0.3
        }.get(category, 0.5)

        # Boost for larger commits
        lines_changed = commit.stats.total["insertions"] + commit.stats.total["deletions"]
        size_boost = min(0.2, lines_changed / 500 * 0.2)

        return min(1.0, base_importance + size_boost)

    def _extract_branch_from_merge(self, message: str) -> str:
        """Extract branch name from merge commit message."""
        # Common formats: "Merge branch 'name'" or "Merge pull request #N from org/branch"
        match = re.search(r"Merge branch '([^']+)'", message)
        if match:
            return match.group(1)

        match = re.search(r"from [^/]+/([^\s]+)", message)
        if match:
            return match.group(1)

        return "unknown"

    def _summarize_branch_work(self, commits: List, branch: str) -> str:
        """Summarize work done in a branch."""
        if not commits:
            return "No commits"

        # Collect commit messages
        messages = [c.message.split('\n')[0] for c in commits]

        # Simple summary: count by type
        types = {"features": 0, "fixes": 0, "refactors": 0, "other": 0}

        for msg in messages:
            msg_lower = msg.lower()
            if "feat" in msg_lower or "add" in msg_lower:
                types["features"] += 1
            elif "fix" in msg_lower or "bug" in msg_lower:
                types["fixes"] += 1
            elif "refactor" in msg_lower:
                types["refactors"] += 1
            else:
                types["other"] += 1

        # Build summary
        parts = []
        if types["features"]:
            parts.append(f"{types['features']} feature(s)")
        if types["fixes"]:
            parts.append(f"{types['fixes']} fix(es)")
        if types["refactors"]:
            parts.append(f"{types['refactors']} refactor(s)")

        return ", ".join(parts) if parts else f"{len(commits)} commit(s)"

    def _install_hook(self, hook_path: Path, script: str) -> bool:
        """Install a git hook script."""
        try:
            # If hook exists, check if it's ours
            if hook_path.exists():
                existing = hook_path.read_text()
                if "llm-memory capture" in existing:
                    # Already installed
                    return True
                else:
                    # Backup existing hook
                    backup = hook_path.with_suffix(".backup")
                    hook_path.rename(backup)

            # Write hook
            hook_path.write_text(script)
            hook_path.chmod(0o755)  # Make executable

            return True
        except Exception as e:
            print(f"Failed to install {hook_path.name}: {e}")
            return False

    def _generate_post_commit_script(self) -> str:
        """Generate post-commit hook script."""
        return f"""#!/bin/sh
# llm-memory auto-capture hook
# Automatically records commits to memory

# Run llm-memory capture in background to avoid slowing down commits
(llm-memory capture git commit HEAD &) 2>/dev/null

exit 0
"""

    def _generate_post_merge_script(self) -> str:
        """Generate post-merge hook script."""
        return f"""#!/bin/sh
# llm-memory auto-capture hook
# Automatically records merges to memory

# Run llm-memory capture in background
(llm-memory capture git merge &) 2>/dev/null

exit 0
"""
