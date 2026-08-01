"""Precision-gated context injection.

Recall and injection are different problems and deserve different thresholds.

*Recall* is user-initiated: someone typed a query and wants to see candidates, so
returning a marginal hit costs them a glance. *Injection* is unsolicited: it spends the
assistant's context on memories nobody asked for, and a wrong memory does not merely
waste tokens -- it actively steers the model. The asymmetry means injection should be
strictly more conservative than search.

This is not a stylistic preference. SWE-ContextBench (arXiv:2602.08316, 1,100 tasks over
51 repositories) measured both sides:

- Curated ("oracle") prior context lifted issue resolution from 26.26% to 34.34%.
- Context the agent selected for itself, unfiltered, scored 12.12 points *below* the
  curated variant, and both unfiltered variants "are more expensive than the no-context
  baseline" while not improving accuracy.

A separate controlled study of persistent memory in coding agents found no quality gain
on simple tasks and net-negative value on trivial codebases; the gains concentrated in
turn and token reduction on complex work.

Together those give three rules, implemented below:

1. **Abstain by default.** When the task is vague, the corpus is thin, or nothing clears
   the relevance floor, inject nothing. Silence is a correct and common answer.
2. **Budget, do not fill.** Cap both the number of memories and their characters. The
   goal is the few memories that change what the assistant does, not everything related.
3. **Gate on complexity.** Skip injection where the evidence says it does not pay.

Every decision is recorded on :class:`InjectionResult` so the policy can be measured
(``scripts/evaluate_oracle_gap.py``) rather than merely asserted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from visp_memory.core.clock import parse_utc
from visp_memory.core.eligibility import filter_recall_eligible, require_repo_id

# Roughly four characters per token for English prose and identifiers. Used only for
# reporting; nothing branches on the exact value.
_CHARS_PER_TOKEN = 4

_STOP_WORDS = frozenset(
    {
        "a", "about", "add", "an", "and", "any", "are", "as", "at", "be", "but", "by",
        "can", "do", "does", "for", "from", "get", "has", "have", "how", "i", "if", "in",
        "is", "it", "its", "just", "make", "me", "my", "need", "not", "of", "on", "or",
        "please", "so", "that", "the", "then", "this", "to", "up", "use", "want", "was",
        "we", "what", "when", "where", "which", "why", "will", "with", "you", "your",
    }
)

# A task must contain at least this many distinct meaningful terms before injection is
# considered. "fix it" or "continue" carries no retrievable signal, so anything recall
# returns for it is matching on noise.
DEFAULT_MIN_TASK_TERMS = 3

# Below this many memories the store cannot represent a project's accumulated context,
# and injection is measuring noise. A fresh `visp-memory init` on a repository with almost
# no history lands here, which is the intended behaviour: stay quiet until there is
# something worth saying.
DEFAULT_MIN_CORPUS = 5

# Calibrated against the scores this project actually produces, not picked by feel.
#
# score_memory_result() blends similarity*0.50 + lexical*0.30 + importance*0.15 +
# recency*0.05. On the default install there is no embedding provider, so `similarity`
# is the neutral constant 0.5 and `lexical` carries all the discrimination. That maps
# the observable range to roughly:
#
#     no lexical overlap      -> ~0.40
#     half the query terms    -> ~0.55
#     full lexical match      -> ~0.70
#
# So 0.55 is "at least half the query's terms are present", which is the weakest match
# worth spending context on. An earlier value of 0.62 was set without measuring and
# would have rejected essentially every keyword-mode result -- safe, but useless.
DEFAULT_MIN_RELEVANCE = 0.55

# The absolute floor alone cannot separate signal from a uniformly mediocre pool: when
# every candidate scores the same, the ranking carries no information and injecting the
# arbitrary top item is exactly the unfiltered-context failure mode. A candidate must
# therefore also stand out from the weakest thing recall returned.
DEFAULT_MIN_MARGIN = 0.04

# ...unless it is a strong match outright, in which case a flat pool does not matter.
DEFAULT_STRONG_RELEVANCE = 0.68

DEFAULT_MAX_MEMORIES = 4
DEFAULT_MAX_CHARS = 1200

# Two candidates sharing this fraction of their meaningful terms are treated as the same
# point. Spending a scarce injection slot restating a selected memory is the most common
# way a budget gets wasted.
DEFAULT_REDUNDANCY_THRESHOLD = 0.7


def _terms(value: str) -> set[str]:
    """Extract meaningful lowercase terms, keeping code-ish tokens intact."""
    if not value:
        return set()
    return {
        term
        for term in re.findall(r"[a-z0-9_./-]+", value.casefold())
        if len(term) > 1 and term not in _STOP_WORDS
    }


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


@dataclass(frozen=True)
class InjectionPolicy:
    """Thresholds governing what may be injected.

    Defaults are deliberately strict. Loosening them is a decision that should be made
    against benchmark numbers, not vibes -- see ``scripts/evaluate_oracle_gap.py``.
    """

    max_memories: int = DEFAULT_MAX_MEMORIES
    max_chars: int = DEFAULT_MAX_CHARS
    min_relevance: float = DEFAULT_MIN_RELEVANCE
    min_margin: float = DEFAULT_MIN_MARGIN
    strong_relevance: float = DEFAULT_STRONG_RELEVANCE
    min_task_terms: int = DEFAULT_MIN_TASK_TERMS
    min_corpus: int = DEFAULT_MIN_CORPUS
    redundancy_threshold: float = DEFAULT_REDUNDANCY_THRESHOLD
    # Warnings are the one category worth surfacing even for a thin task: "this file has
    # a race condition" is valuable precisely when the developer has not said much yet.
    always_allow_warnings: bool = True
    # Provenance quarantine and trust decay. See visp_memory.core.trust: memories that
    # originated outside the repository are never auto-injected, and stale ones fall out
    # of eligibility rather than competing on relevance forever.
    enforce_trust: bool = True
    min_trust: float = 0.35
    # Structural anchoring. A memory naming a file you are editing is relevant for a
    # reason stronger than vocabulary overlap, and one whose anchored code has been
    # deleted is describing something that no longer exists. See core.anchors.
    use_anchors: bool = True
    drop_stale_anchors: bool = True

    @classmethod
    def relaxed(cls) -> "InjectionPolicy":
        """A wider budget for explicit, user-initiated context requests.

        `visp-memory context` is someone asking for the picture; the unsolicited-injection
        asymmetry does not apply.
        """
        return cls(
            max_memories=10,
            max_chars=4000,
            min_relevance=0.45,
            min_margin=0.0,
            min_task_terms=0,
            min_corpus=1,
        )


@dataclass
class InjectionResult:
    """What was injected, what was withheld, and why."""

    memories: list[dict[str, Any]] = field(default_factory=list)
    reason: str = "ok"
    considered: int = 0
    dropped_below_floor: int = 0
    dropped_redundant: int = 0
    dropped_over_budget: int = 0
    dropped_quarantined: int = 0
    dropped_untrusted: int = 0
    dropped_stale_anchor: int = 0
    dropped_ineligible: int = 0
    anchored_hits: int = 0
    eligibility_filter: dict[str, Any] = field(default_factory=dict)

    @property
    def abstained(self) -> bool:
        return not self.memories

    @property
    def chars(self) -> int:
        return sum(len(_content_of(memory)) for memory in self.memories)

    @property
    def estimated_tokens(self) -> int:
        return self.chars // _CHARS_PER_TOKEN

    @property
    def withheld_total(self) -> int:
        return (
            self.dropped_below_floor
            + self.dropped_redundant
            + self.dropped_over_budget
            + self.dropped_quarantined
            + self.dropped_untrusted
            + self.dropped_stale_anchor
            + self.dropped_ineligible
        )

    def summary(self) -> str:
        """One line suitable for showing a developer after a session."""
        if self.abstained:
            return f"No memory injected ({self.reason})."
        withheld = self.withheld_total
        detail = f", {withheld} withheld" if withheld else ""
        return (
            f"Injected {len(self.memories)} of {self.considered} candidate memories "
            f"(~{self.estimated_tokens} tokens{detail})."
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "injected": len(self.memories),
            "considered": self.considered,
            "reason": self.reason,
            "chars": self.chars,
            "estimated_tokens": self.estimated_tokens,
            "dropped_below_floor": self.dropped_below_floor,
            "dropped_redundant": self.dropped_redundant,
            "dropped_over_budget": self.dropped_over_budget,
            "dropped_quarantined": self.dropped_quarantined,
            "dropped_untrusted": self.dropped_untrusted,
            "dropped_stale_anchor": self.dropped_stale_anchor,
            "dropped_ineligible": self.dropped_ineligible,
            "anchored_hits": self.anchored_hits,
            "memory_ids": [memory.get("id") for memory in self.memories],
            "eligibility_filter": self.eligibility_filter,
        }


def _content_of(memory: dict[str, Any]) -> str:
    for key in ("content", "text", "event", "summary"):
        value = memory.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


# The semantic layer's actual warning vocabulary (KnowledgeCategory). There is no
# category literally named "warning"; assuming there was meant this check never fired.
WARNING_CATEGORIES = frozenset({"fragile_area", "known_issue", "gotcha", "warning"})


def _is_warning(memory: dict[str, Any]) -> bool:
    if memory.get("category") in WARNING_CATEGORIES:
        return True
    tags = memory.get("tags") or []
    return isinstance(tags, (list, tuple, set)) and "warning" in tags


def task_signal(task: Optional[str], files: Optional[Iterable[str]] = None) -> int:
    """Count the distinct retrievable terms in a task description.

    File paths are split into components here, unlike in matching where they are kept
    intact. Naming a concrete file is a strong specificity signal -- "why this" against
    ``src/auth/tokens.py`` is a more answerable question than three vague words -- and
    counting the path as a single opaque token would gate it out.
    """
    signal = _terms(task or "")
    for path in files or ():
        signal |= _terms(re.sub(r"[/.\\-]+", " ", str(path)))
    return len(signal)


def select_for_injection(
    candidates: list[dict[str, Any]],
    *,
    repo_id: Optional[str] = None,
    environment: Any = None,
    task_type: Any = None,
    as_of: Any = None,
    task: Optional[str] = None,
    files: Optional[Iterable[str]] = None,
    corpus_size: Optional[int] = None,
    policy: Optional[InjectionPolicy] = None,
    repo_root: Optional[Any] = None,
) -> InjectionResult:
    """Choose the subset of ``candidates`` worth spending context on.

    ``candidates`` are recall results carrying ``relevance_score``, best first.
    ``corpus_size`` is the number of memories available for this scope; when omitted the
    corpus gate is skipped (the caller is asserting the store is populated).

    ``repo_root`` enables staleness checking: memories whose anchored files have all been
    deleted are withheld. Omitted means "do not check the filesystem".
    """
    repo_id = require_repo_id(repo_id)
    policy = policy or InjectionPolicy()
    result = InjectionResult(considered=len(candidates))

    eligibility = filter_recall_eligible(
        candidates,
        repo_id=repo_id,
        environment=environment,
        task_type=task_type,
        as_of=as_of,
    )
    candidates = eligibility.allowed
    result.eligibility_filter = eligibility.diagnostics()
    result.dropped_ineligible = len(eligibility.rejected)

    if not candidates:
        result.reason = (
            "every candidate was temporally invalid or out of scope"
            if eligibility.rejected
            else "no candidates"
        )
        return result

    # Trust gate runs first: a poisoned or stale memory that would have won on relevance
    # is exactly the one that must not reach the prompt. Rejected memories remain fully
    # available to explicit recall -- this bounds injection, it does not hide data.
    if policy.enforce_trust:
        from visp_memory.core.trust import filter_injectable

        candidates, rejected = filter_injectable(candidates, min_trust=policy.min_trust)
        for _memory, assessment in rejected:
            if assessment.quarantined:
                result.dropped_quarantined += 1
            else:
                result.dropped_untrusted += 1

        if not candidates:
            result.reason = (
                "every candidate was quarantined or below the trust threshold"
                if result.dropped_quarantined
                else "no candidate was trusted enough to inject"
            )
            return result

    # Complexity gate, corpus half. A thin store has no accumulated history, so ranked
    # recall over it is measuring noise. It does not follow that the store is worthless:
    # a warning someone wrote by hand, or one derived from a revert, is high-precision
    # regardless of how much else is stored. So a thin corpus restricts injection to
    # warnings rather than disabling it -- gating those out meant a user who recorded
    # three warnings saw none of them.
    thin_corpus = corpus_size is not None and corpus_size < policy.min_corpus

    signal = task_signal(task, files)
    thin_task = signal < policy.min_task_terms

    # Warnings earn an exception to the vagueness gate only when there is a file to scope
    # them to. "fix it" with no file in hand cannot be answered by warnings about four
    # unrelated modules -- that is four confident, irrelevant memories, which is the exact
    # failure this policy exists to prevent.
    warnings_only = thin_corpus or thin_task
    if warnings_only and not policy.always_allow_warnings:
        result.reason = (
            f"corpus too small ({corpus_size} < {policy.min_corpus})"
            if thin_corpus
            else f"task too vague ({signal} terms < {policy.min_task_terms})"
        )
        return result
    if thin_task and not files:
        result.reason = f"task too vague ({signal} terms < {policy.min_task_terms})"
        return result

    # A pool where everything scores alike carries no ranking information, so "the top
    # result" is arbitrary. Require candidates to stand out from the weakest thing recall
    # returned, unless they are strong matches in their own right.
    # The rule is degenerate for a single candidate, which is always its own pool floor
    # and so could never clear its own margin. With one result there is no ranking to
    # distrust, and the absolute floor is the whole test.
    scores = [float(c.get("relevance_score") or 0.0) for c in candidates]
    if len(scores) > 1:
        margin_floor = min(scores) + policy.min_margin
    else:
        margin_floor = 0.0

    # Structural anchoring. A memory that names a file in scope is relevant for a reason
    # stronger than shared vocabulary, so it is considered first and is exempt from the
    # relevance floor -- lexical scoring cannot see the difference between a warning
    # *about* this file and a commit that merely mentions similar words.
    anchored_ids: set[str] = set()
    if policy.use_anchors and files:
        from visp_memory.core.anchors import AnchorIndex

        anchored_ids = AnchorIndex.build(candidates).for_files(files)
        if anchored_ids:
            candidates = sorted(
                candidates,
                key=lambda c: (
                    c.get("id") not in anchored_ids,
                    -float(c.get("relevance_score") or 0.0),
                ),
            )
    result.anchored_hits = len(anchored_ids)

    if policy.drop_stale_anchors and repo_root is not None:
        from visp_memory.core.anchors import inspect as inspect_anchors

        reports = [(c, inspect_anchors(c, repo_root)) for c in candidates]

        # Self-calibration. Staleness is only meaningful if this really is the tree the
        # memories describe. If *nothing* resolves, the far likelier explanation is that
        # we are in the wrong working directory than that the whole codebase was deleted
        # -- and acting on it would silently withhold every anchored memory, which are
        # precisely the most structurally relevant ones. Verified anchors elsewhere in
        # the pool are the evidence that a MISSING verdict can be believed.
        anchored_reports = [r for _c, r in reports if r.anchored]
        trustworthy = any(r.present for r in anchored_reports)

        if trustworthy:
            live: list[dict[str, Any]] = []
            for candidate, report in reports:
                # Only a memory whose *entire* subject has been deleted is withheld: one
                # anchor of three going missing still leaves it about live code.
                if report.fully_stale:
                    result.dropped_stale_anchor += 1
                    continue
                live.append(candidate)
            candidates = live

            if not candidates:
                result.reason = "every candidate described code that no longer exists"
                return result

    selected: list[dict[str, Any]] = []
    selected_terms: list[set[str]] = []
    used_chars = 0

    for candidate in candidates:
        content = _content_of(candidate)
        if not content:
            continue

        warning = _is_warning(candidate)

        # In warnings-only mode nothing else survives: warnings are the category that
        # stays useful when the task is vague or the store is too thin to rank.
        if warnings_only and not warning:
            result.dropped_below_floor += 1
            continue

        score = float(candidate.get("relevance_score") or 0.0)
        strong = score >= policy.strong_relevance
        anchored = candidate.get("id") in anchored_ids
        if not warning and not strong and not anchored:
            if score < policy.min_relevance or score < margin_floor:
                result.dropped_below_floor += 1
                continue

        if len(selected) >= policy.max_memories:
            result.dropped_over_budget += 1
            continue

        candidate_terms = _terms(content)
        if any(
            _jaccard(candidate_terms, chosen) >= policy.redundancy_threshold
            for chosen in selected_terms
        ):
            result.dropped_redundant += 1
            continue

        if used_chars + len(content) > policy.max_chars:
            # A single oversized memory should not consume the whole budget, but a
            # smaller later candidate still might fit.
            result.dropped_over_budget += 1
            continue

        selected.append(candidate)
        selected_terms.append(candidate_terms)
        used_chars += len(content)

    result.memories = selected
    if not selected:
        if thin_corpus:
            result.reason = f"corpus too small ({corpus_size} < {policy.min_corpus})"
        elif thin_task:
            result.reason = f"task too vague ({signal} terms < {policy.min_task_terms})"
        elif result.dropped_below_floor and margin_floor > policy.min_relevance:
            result.reason = "candidates were not distinguishable from each other"
        else:
            result.reason = f"nothing cleared the relevance floor ({policy.min_relevance})"
    return result


# Recall's own default cut-off (0.56) sits just above the "half the query's terms
# matched" band, so genuine hits land marginally under it -- a mined "historically
# fragile" warning measured 0.5549 and was invisible to `recall`. Injection must gather
# below that line and apply its own policy, otherwise the policy never sees the
# candidates it exists to judge.
CANDIDATE_MIN_SCORE = 0.40
CANDIDATE_LIMIT = 20


def gather_candidates(
    memory: Any,
    *,
    task: Optional[str] = None,
    files: Optional[Iterable[str]] = None,
    repo_id: Optional[str] = None,
    limit: int = CANDIDATE_LIMIT,
    environment: Any = None,
    task_type: Any = None,
    as_of: Any = None,
) -> list[dict[str, Any]]:
    """Collect ranked recall candidates for the injection policy to judge.

    Deliberately permissive: filtering is the policy's job, and a candidate that recall
    hides can never be reconsidered.
    """
    query = task or ""
    if files:
        query = f"{query} {' '.join(str(f) for f in files)}".strip()
    if not query:
        return []

    try:
        return memory.recall(
            query=query,
            repo_id=repo_id,
            limit=limit,
            min_score=CANDIDATE_MIN_SCORE,
            task=task,
            files=list(files) if files else None,
            environment=environment,
            task_type=task_type,
            as_of=as_of,
        )
    except Exception:  # fail-open: injection is never worth breaking a session for
        return []


def inject_for_task(
    memory: Any,
    *,
    task: Optional[str] = None,
    files: Optional[Iterable[str]] = None,
    repo_id: Optional[str] = None,
    policy: Optional[InjectionPolicy] = None,
    repo_root: Optional[Any] = None,
    environment: Any = None,
    task_type: Any = None,
    as_of: Any = None,
) -> InjectionResult:
    """Gather candidates and apply the policy in one step.

    ``repo_root`` defaults to the working directory so anchor staleness is checked
    against the tree the developer is actually in.
    """
    repo_id = require_repo_id(repo_id or memory.config.repo_id)
    candidates = gather_candidates(
        memory,
        task=task,
        files=files,
        repo_id=repo_id,
        environment=environment,
        task_type=task_type,
        as_of=as_of,
    )

    if repo_root is None:
        from pathlib import Path as _Path

        repo_root = _Path.cwd()

    corpus_size = None
    try:
        stats = memory._storage.get_stats(repo_id=repo_id)
        total = stats.get("total_memories") if isinstance(stats, dict) else None
        if isinstance(total, int):
            corpus_size = total
    except Exception:
        corpus_size = None

    return select_for_injection(
        candidates,
        task=task,
        files=files,
        corpus_size=corpus_size,
        policy=policy,
        repo_root=repo_root,
        repo_id=repo_id,
        environment=environment,
        task_type=task_type,
        as_of=as_of,
    )


DEFAULT_SESSION_WARNINGS = 3
DEFAULT_SESSION_CHARS = 900


def build_session_brief(
    memory: Any,
    *,
    repo_id: Optional[str] = None,
    max_warnings: int = DEFAULT_SESSION_WARNINGS,
    max_chars: int = DEFAULT_SESSION_CHARS,
    environment: Any = None,
    task_type: Any = None,
    as_of: Any = None,
) -> str:
    """Build the compact brief injected when a coding session begins.

    A session start has no task, so relevance cannot be computed and ranked recall is
    meaningless. Only two things earn a slot without a query:

    - **Explicit intent** (current focus, task, constraints) -- the developer typed it,
      so it is known-relevant and small.
    - **Warnings** -- the "you are about to step on a rake" category, valuable precisely
      because the developer has not said what they are doing yet.

    Recent history is deliberately excluded. It is the highest-volume, lowest-precision
    part of the store, and without a task there is no way to tell which of the last ten
    events matters. The old behaviour concatenated everything and truncated at 4,000
    characters, which both blew the budget and cut sentences in half.

    Returns an empty string when there is nothing worth saying.
    """
    repo_id = require_repo_id(repo_id or memory.config.repo_id)
    sections: list[str] = []

    try:
        summary = memory.intent.summarize(repo_id=repo_id)
    except Exception:
        summary = None

    if summary:
        focus = (summary.get("focus") or {}).get("description")
        current = (summary.get("current_task") or {}).get("description")
        constraints = [c for c in (summary.get("constraints") or []) if c][:3]

        intent_lines = []
        if current:
            intent_lines.append(f"- Working on: {current}")
        if focus and focus != current:
            intent_lines.append(f"- Focus: {focus}")
        for constraint in constraints:
            text = constraint if isinstance(constraint, str) else constraint.get("content", "")
            if text:
                intent_lines.append(f"- Constraint: {text}")
        if intent_lines:
            sections.append("### Current direction\n" + "\n".join(intent_lines))

    try:
        from visp_memory.core.trust import filter_unsolicited

        eligible = filter_recall_eligible(
            memory.semantic.get_warnings(repo_id=repo_id),
            repo_id=repo_id,
            environment=environment,
            task_type=task_type,
            as_of=as_of,
        ).allowed
        warnings = filter_unsolicited(
            eligible, now=parse_utc(as_of) if as_of is not None else None
        ).allowed[:max_warnings]
    except Exception:
        warnings = []

    warning_lines = [f"- {w['content']}" for w in warnings if w.get("content")]
    if warning_lines:
        sections.append("### Warnings\n" + "\n".join(warning_lines))

    if not sections:
        return ""

    brief = "\n\n".join(sections)
    if len(brief) > max_chars:
        # Trim whole lines rather than mid-sentence, so nothing is injected truncated.
        kept: list[str] = []
        used = 0
        for line in brief.splitlines():
            if used + len(line) + 1 > max_chars:
                break
            kept.append(line)
            used += len(line) + 1
        brief = "\n".join(kept).rstrip()
    return brief


# Captured commits render as "Commit: <subject> | Modified: <every path>". The subject
# carries the meaning; the path list can be several times longer and crowds out other
# memories. Trim at the structural boundary rather than mid-word.
PER_MEMORY_CHARS = 220


def _trim(content: str, limit: int = PER_MEMORY_CHARS) -> str:
    if len(content) <= limit:
        return content
    head = content[:limit]
    for boundary in (" | ", ". ", "\n"):
        cut = head.rfind(boundary)
        if cut > limit // 3:
            return head[:cut].rstrip()
    return head.rstrip() + "…"


def format_injection(result: InjectionResult, *, header: str = "Project memory") -> str:
    """Render selected memories as compact markdown, or empty string when abstaining."""
    if result.abstained:
        return ""

    lines = [f"## {header}", ""]
    for memory in result.memories:
        content = _trim(_content_of(memory).strip())
        marker = "warning" if _is_warning(memory) else memory.get("category") or "note"
        memory_id = memory.get("id")
        cite = f" [{memory_id[:8]}]" if isinstance(memory_id, str) and memory_id else ""
        lines.append(f"- **{marker}**{cite}: {content}")
    return "\n".join(lines)
